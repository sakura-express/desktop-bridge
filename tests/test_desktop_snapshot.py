import asyncio
import json

import pytest
from test_app import FakeBrowser, FakeCoding, FakeDesktop

from desktop_bridge.app import Runtime
from desktop_bridge.state import BridgeError


class StructuredDesktop(FakeDesktop):
    size = (1920, 1080)

    async def snapshot(self):
        return {"elements": [{"name": "Downloads", "bounds": [10, 20, 100, 30]}]}

    async def screenshot(self):
        raise AssertionError("structured observation must not capture pixels")


async def test_snapshot_is_text_only_and_authorizes_one_action(tmp_path):
    runtime = Runtime(tmp_path, desktop=StructuredDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    try:
        assert "desktop_snapshot" in {tool.name for tool in runtime.tools()}
        await runtime.call("session_start", {})
        content = await runtime.call("desktop_snapshot", {})
        assert len(content) == 1 and content[0].type == "text"
        observation = json.loads(content[0].text)
        assert observation["width"] == 1920
        action = {"action_id": "first", "observation_id": observation["observation_id"],
                  "action": {"kind": "click", "x": 60, "y": 35}}
        await runtime.call("desktop_action", action)
        await runtime.call("desktop_action", action)
        assert runtime.desktop.calls == 1
        with pytest.raises(BridgeError, match="stale"):
            await runtime.call("desktop_action", {**action, "action_id": "second"})
        await runtime.session.transition("private")
        with pytest.raises(BridgeError, match="paused"):
            await runtime.call("desktop_snapshot", {})
    finally:
        runtime.session.close()


async def test_snapshot_control_change_does_not_issue_observation(tmp_path):
    runtime = Runtime(tmp_path, desktop=StructuredDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    entered, release = asyncio.Event(), asyncio.Event()

    async def snapshot():
        entered.set()
        await release.wait()
        return {"elements": []}

    runtime.desktop.snapshot = snapshot
    try:
        pending = asyncio.create_task(runtime.call("desktop_snapshot", {}))
        await entered.wait()
        await runtime.session.transition("private")
        release.set()
        with pytest.raises(BridgeError, match="Control changed"):
            await pending
        assert not runtime.session.observations
    finally:
        runtime.session.close()


async def test_unsupported_backend_does_not_advertise_snapshot(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    try:
        assert "desktop_snapshot" not in {tool.name for tool in runtime.tools()}
        with pytest.raises(BridgeError, match="does not support"):
            await runtime.call("desktop_snapshot", {})
    finally:
        runtime.session.close()
