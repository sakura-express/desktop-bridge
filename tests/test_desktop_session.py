"""Deployment contracts for a desktop that survives closing Chromium."""

import configparser
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_session_is_independent_of_browser():
    config = configparser.ConfigParser(interpolation=None)
    config.read(ROOT / "docker/supervisord.conf")
    desktop = config["program:desktop"]
    assert desktop["command"] == "/usr/local/bin/start-desktop"
    assert desktop.getboolean("autorestart")
    assert desktop.getboolean("stopasgroup")
    assert desktop.getboolean("killasgroup")
    browser = config["program:browser"]
    assert browser["autorestart"] == "unexpected"
    assert browser["exitcodes"] == "0"


def test_desktop_has_launchers_for_browser_files_and_terminal():
    launchers = ROOT / "docker/desktop"
    expected = {
        "chromium.desktop": "/usr/local/bin/start-browser",
        "files.desktop": "thunar /data/workspace",
        "terminal.desktop": "xterm -e /bin/bash",
    }
    for name, command in expected.items():
        config = configparser.ConfigParser(interpolation=None)
        config.read(launchers / name, encoding="utf-8")
        entry = config["Desktop Entry"]
        assert entry["Type"] == "Application"
        assert entry["Exec"] == command
        assert entry["Terminal"] == "false"
    # Reopening Chromium must retain the MCP CDP endpoint and profile.
    browser = (ROOT / "docker/start-browser.sh").read_text()
    assert "--remote-debugging-port=9222" in browser
    assert "--user-data-dir=/data/profile" in browser


def test_image_contains_desktop_and_session_bus():
    dockerfile = (ROOT / "Dockerfile").read_text()
    for package in (
        "xfce4-session", "xfwm4", "xfce4-panel", "xfdesktop4", "thunar",
        "xfce4-settings", "dbus-x11", "x11-utils",
    ):
        assert package in dockerfile
    script = (ROOT / "docker/start-desktop.sh").read_text()
    assert "xdpyinfo" in script
    assert "exec dbus-run-session -- xfce4-session" in script
    assert b"\r" not in (ROOT / "docker/start-desktop.sh").read_bytes()


def test_panel_is_preconfigured_without_first_run_dialog():
    config = ROOT / "docker/xfce4/xfconf/xfce-perchannel-xml"
    panel = ET.parse(config / "xfce4-panel.xml").getroot()
    assert panel.find("property[@name='configver']").get("value") == "2"
    plugins = panel.find("property[@name='plugins']")
    assert {p.get("value") for p in plugins} >= {
        "applicationsmenu", "tasklist", "clock", "showdesktop",
    }
    session = ET.parse(config / "xfce4-session.xml").getroot()
    save = session.find("property[@name='general']/property[@name='SaveOnExit']")
    assert save.get("value") == "false"
