import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("preview", Path(__file__).parents[1] / "scripts/preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


@pytest.mark.parametrize("url", [
    "http://host.test", "https://host.test/mcp", "https://user:password@host.test",
    "https://host.test/?secret=x", "https://host.test/#fragment", "https://localhost",
    "https://127.0.0.1", "https://host.test:8080", "https://host.test\n.bad",
])
def test_preview_origin_restrictions(url):
    with pytest.raises(ValueError):
        preview.public_origin(url)


def test_preview_origin_and_configuration():
    assert preview.public_origin("https://bridge.example.com/") == "https://bridge.example.com"
    assert preview.configuration({"PREVIEW_MODE": "verify"})[:3] == ("verify", "quick", 60)
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "a" * 40})[:3] == ("preview", "quick", 60)


@pytest.mark.parametrize("env", [
    {}, {"BRIDGE_OWNER_TOKEN": "short"}, {"BRIDGE_OWNER_TOKEN": " " * 40},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "351"},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "0"},
    {"PREVIEW_MODE": "verify", "TUNNEL_KIND": "named", "PREVIEW_PUBLIC_URL": "https://x.test"},
    {"PREVIEW_MODE": "other"},
    {"PREVIEW_MODE": "verify", "PREVIEW_PLATFORM": "windows"},
])
def test_preview_fails_closed(env):
    with pytest.raises(ValueError):
        preview.configuration(env)


def test_preview_accepts_normal_password_and_spaces():
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "eight-OK"})[0] == "preview"
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "test phrase with spaces"})[0] == "preview"


def test_preview_longest_bounded_demo():
    assert preview.configuration({"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "350"})[2] == 350


def test_native_preview_cleanup_and_restart(monkeypatch, tmp_path):
    from unittest.mock import Mock

    service, chrome = Mock(), Mock()
    service.poll.return_value = chrome.poll.return_value = None
    desktop = preview.PreviewDesktop("macos")
    desktop.service, desktop.chrome = service, chrome
    desktop.directory = Mock()
    desktop.env = {"BRIDGE_DATA": str(tmp_path)}
    popen = Mock(return_value=Mock())
    monkeypatch.setattr(preview.subprocess, "Popen", popen)
    desktop.restart()
    service.terminate.assert_called_once()
    chrome.terminate.assert_not_called()
    assert popen.call_args.args[0] == [preview.sys.executable, "-m", "desktop_bridge.app"]
    desktop.service = service
    desktop.close()
    chrome.terminate.assert_called_once()
    desktop.directory.cleanup.assert_called_once()


def test_native_preview_detects_dead_process():
    from unittest.mock import Mock

    desktop = preview.PreviewDesktop("macos")
    desktop.service = Mock()
    desktop.service.poll.return_value = 1
    with pytest.raises(RuntimeError, match="exited"):
        desktop.check()


def test_native_start_uses_own_dynamic_cdp_and_filters_runner_secrets(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, Mock

    chrome, service = Mock(), Mock()
    chrome.poll.return_value = None
    popen = Mock(side_effect=[chrome, service])
    endpoint = "ws://127.0.0.1:12345/devtools/browser/test-only"
    discover = Mock(return_value=endpoint)
    monkeypatch.setitem(preview.sys.modules, "macos_probe", SimpleNamespace(
        ProbeError=RuntimeError, read_devtools_active_port=discover))
    monkeypatch.setattr(preview.sys, "platform", "darwin")
    monkeypatch.setattr(preview.Path, "is_file", lambda _: True)
    monkeypatch.setattr(preview.subprocess, "Popen", popen)
    monkeypatch.setattr(preview.socket, "socket", MagicMock())
    desktop = preview.PreviewDesktop("macos")
    try:
        desktop.start({"PATH": "test-path", "BRIDGE_OWNER_TOKEN": "test-owner",
                       "BRIDGE_PUBLIC_URL": "https://bridge.test",
                       "TUNNEL_TOKEN": "secret", "GITHUB_TOKEN": "secret"})
        args = popen.call_args_list[0].args[0]
        assert "--remote-debugging-port=0" in args
        env = popen.call_args_list[1].kwargs["env"]
        assert env["BRIDGE_CDP_ENDPOINT"] == endpoint
        assert env["BRIDGE_DESKTOP_BACKEND"] == "macos"
        assert env["BRIDGE_BIND"] == "127.0.0.1"
        assert "TUNNEL_TOKEN" not in env and "GITHUB_TOKEN" not in env
        assert discover.call_args.args[0] == preview.Path(desktop.directory.name) / "chrome"
    finally:
        desktop.close()
