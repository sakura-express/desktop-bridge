"""Run inside the image: close Chromium, check XFCE, reopen its desktop launcher."""

import json
import re
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def browser_endpoint():
    try:
        with urlopen("http://127.0.0.1:9222/json/version", timeout=2) as response:
            return json.load(response)["webSocketDebuggerUrl"]
    except (URLError, TimeoutError, OSError):
        return None


def visible_classes():
    clients = subprocess.check_output(
        ["xprop", "-root", "_NET_CLIENT_LIST"], text=True,
    )
    result = set()
    for window in re.findall(r"0x[0-9a-fA-F]+", clients):
        info = subprocess.check_output(["xwininfo", "-id", window], text=True)
        if "IsViewable" not in info:
            continue
        wm_class = subprocess.check_output(
            ["xprop", "-id", window, "WM_CLASS"], text=True,
        )
        result.update(value.lower() for value in re.findall(r'"([^"]+)"', wm_class))
    return result


def wait_for(description, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {description}")


def main():
    endpoint = wait_for("Chromium CDP", browser_endpoint)
    wait_for(
        "visible XFCE desktop and panel",
        lambda: {"xfdesktop", "xfce4-panel"} <= visible_classes(),
    )
    with connect(endpoint, proxy=None) as browser:
        browser.send(json.dumps({"id": 1, "method": "Browser.close"}))
        try:
            browser.recv(timeout=5)
        except ConnectionClosed:
            pass
    wait_for("closed Chromium", lambda: browser_endpoint() is None)
    # Give supervisor time to erroneously restart a normally closed browser.
    time.sleep(4)
    assert browser_endpoint() is None, "Supervisor reopened the closed browser"
    classes = visible_classes()
    assert {"xfdesktop", "xfce4-panel"} <= classes, classes
    assert "chromium" not in classes, classes
    print("PASS: closing Chromium leaves a mapped desktop and taskbar", flush=True)
    for launcher, wm_class in (("files.desktop", "thunar"), ("terminal.desktop", "xterm")):
        subprocess.run(
            ["gio", "launch", f"/home/bridge/Desktop/{launcher}"], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        wait_for(f"visible {wm_class}", lambda wm_class=wm_class: wm_class in visible_classes())
    print("PASS: workspace and terminal launchers open real windows", flush=True)
    # Use the actual .desktop Exec entry, not an unrelated browser command.
    subprocess.run(
        ["gio", "launch", "/home/bridge/Desktop/chromium.desktop"], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_for("reopened Chromium CDP", browser_endpoint)
    wait_for("visible reopened Chromium", lambda: "chromium" in visible_classes())
    print("PASS: desktop browser launcher restores Chromium and MCP CDP", flush=True)


if __name__ == "__main__":
    main()
