import ctypes
from unittest.mock import Mock

import pytest

from desktop_bridge.state import BridgeError
from desktop_bridge.windows import Input, WindowsDesktop


def desktop():
    backend = WindowsDesktop.__new__(WindowsDesktop)
    backend.size = (1920, 1080)
    backend._geometry = Mock(return_value=backend.size)
    backend.user = Mock()
    backend.user.SendInput.return_value = 1
    backend.user.SetCursorPos.return_value = 1
    return backend


def events(backend):
    return [call.args[1]._obj for call in backend.user.SendInput.call_args_list]


def test_input_structure_matches_win32_abi():
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_native_windows_rejects_other_platforms(monkeypatch):
    monkeypatch.setattr("desktop_bridge.windows.sys.platform", "linux")
    with pytest.raises(RuntimeError, match="win32"):
        WindowsDesktop()


def test_windows_physical_pixels_and_geometry_change():
    backend = desktop()
    assert backend.point(1500, 900) == (1500, 900)
    with pytest.raises(BridgeError, match="outside"):
        backend.point(1920, 0)
    backend._geometry.return_value = (1280, 800)
    with pytest.raises(BridgeError, match="Display changed"):
        backend.point(0, 0)


def test_unicode_input_encodes_chinese_and_surrogate_pairs():
    backend = desktop()
    backend._perform({"kind": "type", "text": "中😀"})
    actual = events(backend)
    assert [item.ki.wScan for item in actual] == [0x4E2D, 0x4E2D, 0xD83D, 0xD83D, 0xDE00, 0xDE00]
    assert [item.ki.dwFlags for item in actual] == [4, 6, 4, 6, 4, 6]
    assert all(item.type == 1 and item.ki.wVk == 0 for item in actual)


def test_control_shortcuts_and_extended_keys():
    backend = desktop()
    backend._key(["ctrl", "a"])
    assert [(event.ki.wVk, event.ki.dwFlags) for event in events(backend)] == [
        (17, 0), (65, 0), (65, 2), (17, 2)]
    backend.user.SendInput.reset_mock()
    backend._key(["win", "left"])
    assert [item.ki.dwFlags for item in events(backend)] == [1, 1, 3, 3]


def test_unknown_key_does_not_send_partial_shortcut():
    backend = desktop()
    with pytest.raises(BridgeError, match="Unsupported"):
        backend._key(["ctrl", "bad_key"])
    backend.user.SendInput.assert_not_called()


def test_sendinput_failure_releases_pressed_modifier():
    backend = desktop()
    backend.user.SendInput.side_effect = [1, 0, 1]
    with pytest.raises(BridgeError, match="SendInput"):
        backend._key(["ctrl", "a"])
    assert [(event.ki.wVk, event.ki.dwFlags) for event in events(backend)][-1] == (17, 2)


def test_scroll_ticks_and_button_mapping():
    backend = desktop()
    backend._perform({"kind": "scroll", "x": 120, "y": 250, "dy": 2, "dx": -1})
    assert [(item.mi.dwFlags, item.mi.mouseData) for item in events(backend)] == [
        (0x0800, (-240) & 0xFFFFFFFF), (0x1000, (-120) & 0xFFFFFFFF)]
    backend.user.SendInput.reset_mock()
    backend._perform({"kind": "click", "x": 100, "y": 200, "button": "right"})
    assert [item.mi.dwFlags for item in events(backend)] == [8, 16]


def test_drag_failure_releases_mouse_button(monkeypatch):
    backend = desktop()
    monkeypatch.setattr(backend, "_move", Mock(side_effect=[None, RuntimeError("failed")]))
    with pytest.raises(RuntimeError):
        backend._perform({"kind": "drag", "path": [[0, 0], [100, 100]]})
    assert [item.mi.dwFlags for item in events(backend)] == [2, 4]


def test_capture_rejects_dpi_size_mismatch(monkeypatch):
    from PIL import Image

    backend = desktop()
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **_: Image.new("RGB", (960, 540)))
    with pytest.raises(BridgeError, match="geometry differ"):
        backend._capture()


def test_locked_input_desktop_fails_before_capture_or_input():
    backend = WindowsDesktop.__new__(WindowsDesktop)
    backend.user = Mock()
    backend.user.OpenInputDesktop.return_value = None
    with pytest.raises(BridgeError, match="unavailable or locked"):
        backend._geometry()
    backend.user.SendInput.assert_not_called()


@pytest.mark.parametrize("name,monitors", [("Winlogon", 1), ("Default", 2)])
def test_secure_or_multiple_desktops_are_rejected(name, monitors):
    backend = WindowsDesktop.__new__(WindowsDesktop)
    backend.user = Mock()
    backend.user.OpenInputDesktop.return_value = 42
    backend.user.GetSystemMetrics.return_value = monitors

    def desktop_name(_handle, _index, buffer, _size, _needed):
        buffer.value = name
        return 1

    backend.user.GetUserObjectInformationW.side_effect = desktop_name
    with pytest.raises(BridgeError):
        backend._geometry()
    backend.user.CloseDesktop.assert_called_once_with(42)
