import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop, login

from desktop_bridge.app import Runtime, create_app
from desktop_bridge.auth import digest


@pytest.fixture
def app(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    return create_app(tmp_path, TOKEN, "http://testserver", runtime)


def oauth(client):
    headers = login(client)
    registered = client.post("/register", json={
        "redirect_uris": ["http://127.0.0.1:9999/cb"],
    }).json()
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "client_id": registered["client_id"], "redirect_uri": registered["redirect_uris"][0],
        "response_type": "code", "code_challenge_method": "S256", "code_challenge": challenge,
        "scope": "computer", "resource": "http://testserver/mcp",
    }
    approval = client.post("/authorize", data={**params, "csrf": headers["X-CSRF-Token"]},
                           follow_redirects=False)
    assert approval.status_code == 303
    code = parse_qs(urlsplit(approval.headers["location"]).query)["code"][0]
    response = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "client_id": params["client_id"], "redirect_uri": params["redirect_uri"],
        "resource": params["resource"],
    })
    assert response.status_code == 200
    client.cookies.clear()
    return response.json()["access_token"]


def ticket(client, token):
    response = client.post("/api/viewer/ticket", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert 0 < body["ticket_expires_in"] <= 60
    assert "access_token" not in body["viewer_url"] and token not in body["viewer_url"]
    assert urlsplit(body["viewer_url"]).query == ""
    return parse_qs(urlsplit(body["viewer_url"]).fragment)["ticket"][0]


def redeem(client, value, **kwargs):
    return client.post("/api/viewer/session", json={"ticket": value},
                       headers={"Origin": "http://testserver", **kwargs})


def test_oauth_ticket_cookie_and_owner_permission_separation(app):
    with TestClient(app) as client:
        token = oauth(client)
        value = ticket(client, token)
        response = redeem(client, value)
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        assert "bridge_session=" not in response.headers["set-cookie"]
        assert client.get("/api/viewer/status").json()["read_only"] is True
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/personal").status_code == 401
        assert client.get("/api/artifacts").status_code == 401
        assert client.post("/api/control/human").status_code == 401
        assert client.post("/api/control/private").status_code == 401
        assert redeem(client, value).status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/desktop/control", headers={"Origin": "http://testserver"}):
                pytest.fail("Viewer gained control")
        app.state.auth.revoke()
        assert client.get("/api/viewer/status").status_code == 401


def test_ticket_requires_oauth_and_exchange_requires_same_origin_json(app):
    with TestClient(app) as client:
        assert client.post("/api/viewer/ticket").status_code == 401
        assert client.post("/api/viewer/ticket", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401
        token = oauth(client)
        value = ticket(client, token)
        for origin in [None, "https://evil.example", "null"]:
            headers = {"Origin": origin} if origin else {}
            assert client.post("/api/viewer/session", json={"ticket": value}, headers=headers).status_code == 401
        assert client.post("/api/viewer/session", data={"ticket": value},
                           headers={"Origin": "http://testserver"}).status_code == 401
        assert client.post("/api/viewer/session", content="broken", headers={
            "Origin": "http://testserver", "Content-Type": "application/json",
        }).status_code == 401
        assert redeem(client, value).status_code == 200
        metadata = client.get("/.well-known/oauth-protected-resource").json()
        assert metadata["desktop_viewer"]["ticket_endpoint"] == "http://testserver/api/viewer/ticket"
        assert metadata["resource"] == "http://testserver/mcp"


@pytest.mark.parametrize("reason", ["expire", "revoke", "private"])
def test_readonly_websocket_forwarding_and_live_authorization(app, monkeypatch, reason):
    import desktop_bridge.app as app_module

    ports = []
    writes = []

    class Writer:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            reader.feed_data(b"ack")

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def open_connection(host, port):
        nonlocal reader
        reader = asyncio.StreamReader()
        reader.feed_data(b"frame")
        ports.append(port)
        return reader, Writer()

    reader = None
    monkeypatch.setattr(app_module.asyncio, "open_connection", open_connection)
    with TestClient(app) as client:
        token = oauth(client)
        value = ticket(client, token)
        assert redeem(client, value).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/desktop/oauth/view", headers={"Origin": "https://evil.example"}):
                pytest.fail("Foreign origin accepted")
        with client.websocket_connect("/desktop/oauth/view", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_bytes() == b"frame"
            ws.send_bytes(b"rfb-handshake")
            assert ws.receive_bytes() == b"ack"
            assert ports == [5901] and writes == [b"rfb-handshake"]
            if reason == "expire":
                app.state.auth.tokens[digest(token)] = 0
            elif reason == "revoke":
                app.state.auth.revoke()
            else:
                client.portal.call(app.state.runtime.control, "private")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_bytes()
        if reason == "private":
            assert client.get("/api/viewer/status").json()["error"] == "private_takeover"
            assert client.post("/api/viewer/ticket", headers={"Authorization": "Bearer " + token}).status_code == 400
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/desktop/oauth/view", headers={"Origin": "http://testserver"}):
                    pytest.fail("Private desktop visible")


@pytest.mark.parametrize("phase", ["tasks", "writer"])
def test_private_websocket_cancellation_finishes_cleanup(app, monkeypatch, phase):
    import desktop_bridge.app as app_module

    entered = anyio.Event()
    release = anyio.Event()
    completed = []
    request_scopes = []
    forwarding_tasks = []

    class CancellationProbe:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "websocket":
                return await self.app(scope, receive, send)
            with anyio.CancelScope() as cancel_scope:
                request_scopes.append(cancel_scope)
                await self.app(scope, receive, send)

    app.add_middleware(CancellationProbe)
    original_gather = asyncio.gather

    async def gather(*tasks, **kwargs):
        forwarding_tasks.extend(tasks)
        if phase == "tasks":
            entered.set()
            await release.wait()
        result = await original_gather(*tasks, **kwargs)
        completed.append("tasks")
        return result

    class Writer:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            completed.append("close")

        async def wait_closed(self):
            if phase == "writer":
                entered.set()
                await release.wait()
            completed.append("writer")

    async def open_connection(host, port):
        reader = asyncio.StreamReader()
        reader.feed_data(b"frame")
        return reader, Writer()

    async def wait_for_cleanup():
        with anyio.fail_after(5):
            await entered.wait()

    async def cancel_during_cleanup():
        request_scopes[-1].cancel()
        # Deliver request cancellation while cleanup is suspended, then allow
        # shielded cleanup to finish. No wall-clock timing is needed.
        await anyio.sleep(0)
        release.set()

    monkeypatch.setattr(app_module.asyncio, "open_connection", open_connection)
    with TestClient(app) as client:
        token = oauth(client)
        assert redeem(client, ticket(client, token)).status_code == 200
        with client.websocket_connect("/desktop/oauth/view", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_bytes() == b"frame"
            # Patch only while this connection is active, not lifespan shutdown.
            with monkeypatch.context() as cleanup_patch:
                cleanup_patch.setattr(app_module.asyncio, "gather", gather)
                client.portal.call(app.state.runtime.control, "private")
                ws.send_bytes(b"stop")
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_bytes()
                client.portal.call(wait_for_cleanup)
                client.portal.call(cancel_during_cleanup)
        assert completed == ["tasks", "close", "writer"]
        assert forwarding_tasks and all(task.done() for task in forwarding_tasks)
        assert not app.state.runtime.session.sockets
