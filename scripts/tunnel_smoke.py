"""Authenticated, disposable integration checks over a real public HTTPS origin."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
from websockets.sync.client import connect


def smoke(base, owner, *, native_pid=None, native_endpoint=None):
    with httpx.Client(base_url=base, timeout=45, follow_redirects=False) as http:
        denied = http.post("/mcp", json={})
        assert denied.status_code == 401
        assert base + "/.well-known/oauth-protected-resource" in denied.headers["www-authenticate"]
        metadata = http.get("/.well-known/oauth-protected-resource/mcp").json()
        assert metadata["resource"] == base + "/mcp"
        auth = http.get("/.well-known/oauth-authorization-server").json()
        assert auth["issuer"] == base and auth["token_endpoint_auth_methods_supported"] == ["none"]
        assert "S256" in auth["code_challenge_methods_supported"]
        registered = http.post("/register", json={
            "client_name": "Tunnel readiness check",
            "redirect_uris": ["http://127.0.0.1:43111/callback"],
            "token_endpoint_auth_method": "none",
        })
        registered.raise_for_status()
        client = registered.json()
        verifier = secrets.token_urlsafe(48)
        params = {
            "client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
            "response_type": "code", "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
            "resource": base + "/mcp", "scope": "computer", "state": "tunnel-readiness",
        }
        redirect = http.get("/authorize", params=params)
        assert redirect.status_code in {302, 307}
        pending = parse_qs(urlsplit(redirect.headers["location"]).query)["authorize"][0]
        assert pending.startswith(base + "/authorize?"), pending
        login = http.post("/api/login", json={"token": owner})
        login.raise_for_status()
        assert "Secure" in login.headers["set-cookie"]
        csrf = login.json()["csrf"]
        headers = {"X-CSRF-Token": csrf, "Origin": base}
        approval = http.post("/authorize", data={**params, "csrf": csrf})
        assert approval.status_code == 303
        query = parse_qs(urlsplit(approval.headers["location"]).query)
        assert query["state"] == ["tunnel-readiness"]
        response = http.post("/token", data={
            "grant_type": "authorization_code", "code": query["code"][0],
            "client_id": client["client_id"], "redirect_uri": params["redirect_uri"],
            "resource": base + "/mcp", "code_verifier": verifier,
        })
        response.raise_for_status()
        bearer = response.json()["access_token"]
        mcp_headers = {"Authorization": "Bearer " + bearer, "Accept": "application/json, text/event-stream"}
        counter = 0

        def rpc(method, params):
            nonlocal counter
            counter += 1
            response = http.post("/mcp", headers=mcp_headers, json={
                "jsonrpc": "2.0", "id": counter, "method": method, "params": params,
            })
            response.raise_for_status()
            assert "application/json" in response.headers["content-type"], "Quick tunnels require JSON, not SSE"
            data = response.json()
            assert "error" not in data, data
            return data["result"]

        initialized = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "tunnel-smoke", "version": "1"}})
        mcp_headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        names = {item["name"] for item in rpc("tools/list", {})["tools"]}
        assert {"desktop_screenshot", "browser_snapshot", "coding_exec_command", "session_start"} <= names
        assert not rpc("tools/call", {"name": "session_start", "arguments": {}}).get("isError")
        shot = rpc("tools/call", {"name": "desktop_screenshot", "arguments": {}})
        assert any(item["type"] == "image" for item in shot["content"])
        assert not rpc("tools/call", {"name": "browser_snapshot", "arguments": {}}).get("isError")
        if native_pid is not None:
            native_actions(rpc, native_pid, native_endpoint)
        command = rpc("tools/call", {"name": "coding_exec_command", "arguments": {
            "cmd": "printf tunnel-ready", "yield_time_ms": 1000, "bridge_action_id": "public-tunnel-smoke",
        }})
        assert not command.get("isError") and "tunnel-ready" in str(command)
        cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
        with connect(base.replace("https://", "wss://", 1) + "/desktop/view", origin=base,
                     additional_headers={"Cookie": cookie}, subprotocols=["binary"], open_timeout=30) as ws:
            native = http.get("/healthz").json().get("desktop_transport") == "native"
            assert ws.recv(timeout=30).startswith(b"\x89PNG\r\n\x1a\n" if native else b"RFB ")
        # Leave a clean, ready-to-authorize UI. No test access token remains valid.
        http.post("/api/control/paused", headers=headers).raise_for_status()
        http.post("/api/logout", headers=headers).raise_for_status()
        assert http.post("/mcp", headers=mcp_headers, json={}).status_code == 401
    print("PASS public HTTPS: OAuth discovery, PKCE, JSON MCP, real screenshot/browser/shell, desktop WebSocket, revocation", flush=True)


def native_actions(rpc, pid, endpoint):
    """Verify the actual MCP adapter's input against DOM and captured color markers."""
    import io
    import json
    import tempfile
    import time
    from pathlib import Path

    from AppKit import NSRunningApplication
    from macos_probe import MARKERS, axis_mapping, find_marker, prepare_fixture
    from PIL import Image
    from playwright.sync_api import sync_playwright

    def call(name, arguments=None):
        result = rpc("tools/call", {"name": name, "arguments": arguments or {}})
        assert not result.get("isError"), result
        return result

    def metadata(result):
        return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))

    with tempfile.TemporaryDirectory(prefix="bridge-mcp-fixture-") as directory:
        _, url = prepare_fixture(Path(directory))
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(endpoint)
            # Prepare a local test tab; all verified input goes through public MCP.
            page = browser.contexts[0].new_page()
            page.goto(url)
            page.bring_to_front()
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            assert app is not None and app.activateWithOptions_(2), "Chrome activation failed"
            page.wait_for_selector("#a")

            def act(action, selector=None, end=None):
                assert app.activateWithOptions_(2), "Chrome activation failed"
                time.sleep(.2)
                assert app.isActive(), "Own Chrome is not foreground"
                shot = call("desktop_screenshot")
                data = next(item["data"] for item in shot["content"] if item["type"] == "image")
                with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                    image = image.convert("RGB")
                    screen = [find_marker(image.get_flattened_data(), *image.size, color)
                              for color in MARKERS.values()]
                boxes = [page.locator(f"#{key}").bounding_box() for key in MARKERS]
                mapping = axis_mapping([(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                                        for b in boxes], screen)

                def point(target):
                    box = page.locator(target).bounding_box()
                    sx, ox, sy, oy = mapping
                    return [round((box["x"] + box["width"] / 2) * sx + ox),
                            round((box["y"] + box["height"] / 2) * sy + oy)]

                if end:
                    action["path"] = [point(selector), point(end)]
                elif selector:
                    action["x"], action["y"] = point(selector)
                call("desktop_action", {"action": action,
                                        "observation_id": metadata(shot)["observation_id"],
                                        "action_id": secrets.token_hex(16)})

            act({"kind": "click"}, "#click")
            page.wait_for_function("probe.clicks === 1")
            act({"kind": "click"}, "#text")
            act({"kind": "type", "text": "MCP 中文输入"})
            page.wait_for_function("input.value === 'MCP 中文输入'")
            act({"kind": "key", "keys": ["cmd", "a"]})
            act({"kind": "type", "text": "MCP 中文替换"})
            page.wait_for_function("input.value === 'MCP 中文替换'")
            act({"kind": "scroll", "dy": 6}, "#scroll")
            page.wait_for_function("probe.scroll > 0")
            act({"kind": "drag"}, "#drag", "#drop")
            page.wait_for_function("probe.drag && probe.dragMoves > 0")
            page.close()
        print("PASS native macOS input via public MCP: click, Chinese paste, Cmd+A, scroll, drag",
              flush=True)
