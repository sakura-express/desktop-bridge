from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop_bridge.macos import MacDesktop
from desktop_bridge.state import BridgeError


def desktop():
    backend = MacDesktop.__new__(MacDesktop)
    backend.size = (2048, 1536)
    backend.bounds = (0, 0, 1024, 768)
    q = Mock()
    for name, value in {
        "kCGHIDEventTap": 0, "kCGMouseButtonLeft": 0, "kCGMouseButtonRight": 1,
        "kCGMouseButtonCenter": 2, "kCGEventMouseMoved": 5,
        "kCGEventLeftMouseDown": 1, "kCGEventLeftMouseUp": 2,
        "kCGEventLeftMouseDragged": 6, "kCGEventRightMouseDown": 3,
        "kCGEventRightMouseUp": 4, "kCGEventRightMouseDragged": 7,
        "kCGEventOtherMouseDown": 25, "kCGEventOtherMouseUp": 26,
        "kCGEventOtherMouseDragged": 27, "kCGEventFlagMaskCommand": 1 << 20,
        "kCGEventFlagMaskControl": 1 << 18, "kCGEventFlagMaskShift": 1 << 17,
        "kCGEventFlagMaskAlternate": 1 << 19, "kCGScrollEventUnitPixel": 0,
    }.items():
        setattr(q, name, value)
    q.CGGetActiveDisplayList.return_value = (0, [1], 1)
    q.CGDisplayBounds.return_value = SimpleNamespace(
        origin=SimpleNamespace(x=0, y=0), size=SimpleNamespace(width=1024, height=768))
    backend.q = q
    return backend


def test_retina_pixel_mapping_and_display_change():
    backend = desktop()
    assert backend.point(1024, 768) == (512, 384)
    with pytest.raises(BridgeError, match="outside"):
        backend.point(2048, 0)
    backend.q.CGGetActiveDisplayList.return_value = (0, [1, 2], 2)
    with pytest.raises(BridgeError, match="one active display"):
        backend.point(0, 0)
    backend.q.CGGetActiveDisplayList.return_value = (0, [1], 1)
    backend.bounds = (0, 0, 1280, 800)
    with pytest.raises(BridgeError, match="Display changed"):
        backend.point(0, 0)


def test_scroll_direction_and_button_mapping():
    backend = desktop()
    backend._perform({"kind": "scroll", "x": 100, "y": 200, "dy": 2, "dx": -1})
    backend.q.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 0, 2, -60, 30)
    backend._perform({"kind": "click", "x": 100, "y": 200, "button": "right"})
    assert backend.q.CGEventCreateMouseEvent.call_args_list[-2].args == (None, 3, (50, 100), 1)


def test_unknown_key_has_no_partial_input():
    backend = desktop()
    with pytest.raises(BridgeError, match="Unsupported"):
        backend._key(["cmd", "unknown"])
    backend.q.CGEventPost.assert_not_called()


def test_chinese_input_uses_utf8_and_command_paste(monkeypatch):
    backend = desktop()
    run = Mock()
    monkeypatch.setattr("desktop_bridge.macos.subprocess.run", run)
    backend._perform({"kind": "type", "text": "中文输入"})
    assert run.call_args.kwargs["input"] == "中文输入".encode()
    assert backend.q.CGEventCreateKeyboardEvent.call_args_list[0].args == (None, 55, True)
    assert backend.q.CGEventCreateKeyboardEvent.call_args_list[1].args == (None, 9, True)


def test_drag_releases_button_after_failure(monkeypatch):
    backend = desktop()
    mouse = Mock(side_effect=[None, None, RuntimeError("event failed"), None])
    monkeypatch.setattr(backend, "_mouse", mouse)
    with pytest.raises(RuntimeError):
        backend._perform({"kind": "drag", "path": [[0, 0], [100, 100]]})
    assert mouse.call_args.args == (2, (50, 50), 0)
