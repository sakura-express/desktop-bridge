"""Native single-display macOS adapter. Public coordinates are screenshot pixels."""
from __future__ import annotations

import asyncio
import ctypes
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .state import BridgeError

KEYS = dict(zip("asdfhgzxcvbqwerty",
                [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 17, 16], strict=True))
KEYS.update(dict(zip("1234567890", [18, 19, 20, 21, 23, 22, 26, 28, 25, 29], strict=True)))
KEYS.update(dict(zip("uoiplnmjk", [32, 31, 34, 35, 37, 45, 46, 38, 40], strict=True)))
KEYS.update(enter=36, return_=36, tab=48, space=49, backspace=51, escape=53,
            esc=53, delete=117, home=115, end=119, pageup=116, pagedown=121,
            left=123, right=124, down=125, up=126)
KEYS["return"] = 36
KEYS.update(dict(zip([f"f{i}" for i in range(1, 13)],
                     [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111], strict=True)))


class MacDesktop:
    transport = "native"

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("Native macOS desktop requires darwin")
        import Quartz

        self.q = Quartz
        screen = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        framework = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        trusted = framework.AXIsProcessTrusted
        trusted.restype = ctypes.c_bool
        if (screen and not screen()) or not trusted():
            raise RuntimeError("macOS requires Screen Recording and Accessibility permission")
        self.size = None
        self.bounds = None
        self._geometry()

    def _geometry(self):
        q = self.q
        error, _, count = q.CGGetActiveDisplayList(16, None, None)
        if error or count != 1:
            raise BridgeError("DESKTOP_ERROR", "macOS preview requires exactly one active display")
        rect = q.CGDisplayBounds(q.CGMainDisplayID())
        return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)

    def _capture(self):
        from PIL import Image

        bounds = self._geometry()
        with tempfile.TemporaryDirectory(prefix="bridge-screen-") as directory:
            path = Path(directory) / "screen.png"
            subprocess.run(["/usr/sbin/screencapture", "-x", "-D", "1", str(path)],
                           check=True, timeout=15, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            with Image.open(path) as image:
                self.size, self.bounds = image.size, bounds
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                return output.getvalue()

    async def screenshot(self):
        return await asyncio.to_thread(self._capture)

    def point(self, x, y):
        if self.size is None or self.bounds != self._geometry():
            raise BridgeError("STALE_OBSERVATION", "Display changed; take a fresh screenshot")
        width, height = self.size
        if not (0 <= x < width and 0 <= y < height):
            raise BridgeError("DESKTOP_ERROR", "Coordinates outside captured display")
        bx, by, bw, bh = self.bounds
        return bx + x * bw / width, by + y * bh / height

    def _mouse(self, kind, point, button):
        q = self.q
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, kind, point, button))

    def _key(self, keys):
        q = self.q
        modifiers = {"cmd": (55, q.kCGEventFlagMaskCommand),
                     "command": (55, q.kCGEventFlagMaskCommand),
                     "meta": (55, q.kCGEventFlagMaskCommand),
                     "ctrl": (59, q.kCGEventFlagMaskControl),
                     "control": (59, q.kCGEventFlagMaskControl),
                     "alt": (58, q.kCGEventFlagMaskAlternate),
                     "option": (58, q.kCGEventFlagMaskAlternate),
                     "shift": (56, q.kCGEventFlagMaskShift)}
        codes, flags = [], 0
        for key in keys:
            key = key.lower()
            if key in modifiers:
                code, flag = modifiers[key]
                flags |= flag
            elif key in KEYS:
                code = KEYS[key]
            else:
                raise BridgeError("DESKTOP_ERROR", f"Unsupported macOS key: {key}")
            codes.append(code)
        pressed = []
        try:
            for code in codes:
                event = q.CGEventCreateKeyboardEvent(None, code, True)
                q.CGEventSetFlags(event, flags)
                q.CGEventPost(q.kCGHIDEventTap, event)
                pressed.append(code)
        finally:
            for code in reversed(pressed):
                event = q.CGEventCreateKeyboardEvent(None, code, False)
                q.CGEventSetFlags(event, 0)
                q.CGEventPost(q.kCGHIDEventTap, event)

    def _perform(self, action):
        q, kind = self.q, action["kind"]
        button = {"left": q.kCGMouseButtonLeft, "right": q.kCGMouseButtonRight,
                  "middle": q.kCGMouseButtonCenter}[action.get("button", "left")]
        down, up, drag = {
            "left": (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp, q.kCGEventLeftMouseDragged),
            "right": (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp, q.kCGEventRightMouseDragged),
            "middle": (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp, q.kCGEventOtherMouseDragged),
        }[action.get("button", "left")]
        if kind in {"move", "click", "scroll"}:
            point = self.point(action["x"], action["y"])
            self._mouse(q.kCGEventMouseMoved, point, button)
            if kind == "click":
                for count in range(1, action.get("count", 1) + 1):
                    for event_kind in (down, up):
                        event = q.CGEventCreateMouseEvent(None, event_kind, point, button)
                        q.CGEventSetIntegerValueField(event, q.kCGMouseEventClickState, count)
                        q.CGEventPost(q.kCGHIDEventTap, event)
            elif kind == "scroll":
                event = q.CGEventCreateScrollWheelEvent(
                    None, q.kCGScrollEventUnitPixel, 2,
                    -action.get("dy", 0) * 30, -action.get("dx", 0) * 30)
                q.CGEventPost(q.kCGHIDEventTap, event)
        elif kind == "drag":
            points = [self.point(*p) for p in action["path"]]
            self._mouse(q.kCGEventMouseMoved, points[0], button)
            self._mouse(down, points[0], button)
            try:
                for start, end in zip(points, points[1:], strict=False):
                    for step in range(1, 11):
                        point = tuple(a + (b - a) * step / 10
                                      for a, b in zip(start, end, strict=True))
                        self._mouse(drag, point, button)
                        time.sleep(.015)
            finally:
                self._mouse(up, points[-1], button)
        elif kind == "type":
            subprocess.run(["/usr/bin/pbcopy"], input=action["text"].encode("utf-8"),
                           env={**os.environ, "LC_CTYPE": "en_US.UTF-8"}, check=True, timeout=5)
            self._key(["cmd", "v"])
        elif kind == "key":
            self._key(action["keys"])
        return {"ok": True}

    async def perform(self, action):
        return await asyncio.to_thread(self._perform, action)
