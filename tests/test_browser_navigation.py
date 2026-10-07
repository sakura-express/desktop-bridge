import asyncio
import tempfile
from pathlib import Path
from desktop_bridge.backends import Browser, _safe_url, _extract_redirect_chain
from desktop_bridge.state import Session, BridgeError, Observation


def test_safe_url_strips_credentials():
    assert _safe_url("https://user:pass@example.com/page?q=1#hash") == "https://example.com/page?q=1#hash"
    assert _safe_url("http://admin:secret@127.0.0.1:8080/") == "http://127.0.0.1:8080/"
    assert _safe_url("https://example.com") == "https://example.com"
    assert _safe_url(None) is None


def test_extract_redirect_chain():
    class FakeReq:
        def __init__(self, url, redirected_from=None):
            self.url = url
            self.redirected_from = redirected_from

    class FakeResp:
        def __init__(self, req):
            self.request = req

    r1 = FakeReq("https://initial.com/page")
    r2 = FakeReq("https://redirect1.com/page", redirected_from=r1)
    r3 = FakeReq("https://final.com/target", redirected_from=r2)
    resp = FakeResp(r3)
    chain = _extract_redirect_chain(resp)
    assert chain == ["https://initial.com/page", "https://redirect1.com/page"]


async def test_session_idempotency_and_stale_recovery():
    with tempfile.TemporaryDirectory() as td:
        s = Session(Path(td) / "receipts.sqlite3", observation_ttl=0.05)
        await s.transition("agent")

        # 1. Successful action with valid receipt
        async with s.action("act-unique-1", {"action": "test"}) as ticket:
            ticket["result"] = {"ok": True, "value": 42}

        # 2. Same action_id with same payload -> returns cached receipt
        async with s.action("act-unique-1", {"action": "test"}) as ticket:
            assert "cached" in ticket
            assert ticket["cached"]["value"] == 42

        # 3. Same action_id with differing payload -> IDEMPOTENCY_CONFLICT with recovery
        try:
            async with s.action("act-unique-1", {"action": "different"}) as ticket:
                pass
            assert False, "Should raise IDEMPOTENCY_CONFLICT"
        except BridgeError as e:
            assert e.code == "IDEMPOTENCY_CONFLICT"
            assert "Recovery:" in str(e)
            assert "generate a brand-new unique action_id" in str(e)

        # 4. Observation invalidation after action (epoch incremented)
        obs_id = s.observe()
        async with s.action("act-unique-2", {"action": "step1"}, observation_id=obs_id) as ticket:
            pass
        # Prior observation must now be stale
        try:
            async with s.action("act-unique-3", {"action": "step2"}, observation_id=obs_id) as ticket:
                pass
            assert False, "Should raise STALE_OBSERVATION"
        except BridgeError as e:
            assert e.code == "STALE_OBSERVATION"
            assert "Recovery:" in str(e)
            assert "call browser_snapshot" in str(e)

        s.close()


async def test_browser_perform_and_snapshot_simulation():
    # Simulate a fake page to test Browser.perform navigate logic
    class FakeLocator:
        async def aria_snapshot(self):
            return "body aria snapshot text"

    class FakePage:
        def __init__(self, url="https://final.com", status=200):
            self.url = url
            self.status = status
            self.closed = False
        def locator(self, sel):
            return FakeLocator()
        async def title(self):
            return "Page Title"
        async def goto(self, url, wait_until=None, timeout=None):
            if "timeout" in url:
                raise TimeoutError("Simulated timeout")
            if "fail" in url:
                raise RuntimeError("Simulated connection refused")
            self.url = url
            class Resp:
                def __init__(self, s):
                    self.status = s
                    self.request = None
            return Resp(self.status)

    b = Browser()
    b._tabs = {"tab-1": FakePage("https://example.com", 200)}
    b.page = lambda expected_tab_id: asyncio.sleep(0, result=b._tabs[expected_tab_id])
    b._live_tabs = lambda: list(b._tabs.items())
    obs = Observation(epoch=0, at=0, browser_tab_id="tab-1", browser_tab_ids=("tab-1",))

    # 1. Normal navigation HTTP 200
    res = await b.perform({"kind": "navigate", "url": "https://example.com/ok"}, observation=obs, guard=lambda: None)
    assert res["ok"] is True
    assert res["http_status"] == 200
    assert res["requested_url"] == "https://example.com/ok"
    assert res["url"] == "https://example.com/ok"

    # 2. HTTP 404 / 500 error does NOT pretend to be ok: true
    b._tabs = {"tab-1": FakePage("https://example.com/404", 404)}
    res404 = await b.perform({"kind": "navigate", "url": "https://example.com/404"}, observation=obs, guard=lambda: None)
    assert res404["ok"] is False
    assert res404["http_status"] == 404
    assert res404["error"] == "HTTP 404"

    # 3. Timeout is converted to BridgeError NAVIGATION_TIMEOUT
    b._tabs = {"tab-1": FakePage("https://timeout.com", 200)}
    try:
        await b.perform({"kind": "navigate", "url": "https://timeout.com"}, observation=obs, guard=lambda: None)
        assert False, "Should raise NAVIGATION_TIMEOUT"
    except BridgeError as e:
        assert e.code == "NAVIGATION_TIMEOUT"
        assert "Page may have navigated" in str(e)

    # 4. Failed connection converted to BridgeError NAVIGATION_FAILED
    b._tabs = {"tab-1": FakePage("https://fail.com", 200)}
    try:
        await b.perform({"kind": "navigate", "url": "https://fail.com"}, observation=obs, guard=lambda: None)
        assert False, "Should raise NAVIGATION_FAILED"
    except BridgeError as e:
        assert e.code == "NAVIGATION_FAILED"


async def main():
    test_safe_url_strips_credentials()
    test_extract_redirect_chain()
    await test_session_idempotency_and_stale_recovery()
    await test_browser_perform_and_snapshot_simulation()
    print("ALL PYTHON DESKTOP-BRIDGE TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
