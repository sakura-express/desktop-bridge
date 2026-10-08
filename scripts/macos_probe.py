"""Experimental native macOS desktop probe; never a macOS MCP backend."""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

MARKERS = {"a": (241, 17, 91), "b": (19, 229, 137), "c": (37, 83, 243)}
HTML = b"""<!doctype html><meta charset=utf-8><title>Native desktop probe</title>
<style>body{margin:0;background:white;font:18px sans-serif} .marker{position:fixed;width:24px;height:24px}
#a{left:30px;top:30px;background:rgb(241,17,91)}
#b{left:330px;top:30px;background:rgb(19,229,137)}
#c{left:30px;top:330px;background:rgb(37,83,243)}
#controls{position:absolute;left:85px;top:90px}input{width:260px;height:36px}
#scroll{position:absolute;left:420px;top:90px;width:180px;height:180px;overflow:auto;border:3px solid black}
#drag{position:absolute;left:100px;top:250px;width:60px;height:60px;background:#888;touch-action:none}
#drop{position:absolute;left:220px;top:250px;width:100px;height:65px;border:3px dashed black}
</style><i class=marker id=a></i><i class=marker id=b></i><i class=marker id=c></i>
<div id=controls><button id=click>Native click</button><br><input id=text></div>
<div id=scroll><div style='height:1200px'>Native scroll</div></div>
<div id=drag>Drag</div><div id=drop>Drop here</div>
<script>
window.probe={clicks:0,inputs:[],keys:[],scroll:0,drag:false,dragMoves:0};
const input=document.getElementById('text'), scroller=document.getElementById('scroll');
document.getElementById('click').onclick=()=>probe.clicks++;
input.oninput=()=>probe.inputs.push(input.value);
input.onkeydown=e=>probe.keys.push({key:e.key,meta:e.metaKey});
scroller.onscroll=()=>probe.scroll=scroller.scrollTop;
let dragging=false;const d=document.getElementById('drag');
d.onpointerdown=e=>{dragging=true;d.setPointerCapture(e.pointerId)};
d.onpointermove=e=>{if(dragging){probe.dragMoves++;d.style.left=(e.clientX-30)+'px';d.style.top=(e.clientY-30)+'px'}};
d.onpointerup=e=>{const r=document.getElementById('drop').getBoundingClientRect();probe.drag=dragging&&e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom;dragging=false};
</script>"""


class ProbeError(RuntimeError):
    pass


def require_macos(platform: str = sys.platform) -> None:
    if platform != "darwin":
        raise ProbeError("platform: native desktop probe requires macOS (darwin)")


def find_marker(pixels, width, height, color, tolerance=8):
    """Find one solid rectangular component, rejecting absent/ambiguous markers."""
    matches = {i for i, p in enumerate(pixels)
               if all(abs(p[c] - color[c]) <= tolerance for c in range(3))}
    candidates = []
    while matches:
        seed = matches.pop()
        component, pending = [seed], [seed]
        while pending:
            i = pending.pop()
            x, y = i % width, i // width
            for j in (i - 1 if x else -1, i + 1 if x + 1 < width else -1,
                      i - width if y else -1, i + width if y + 1 < height else -1):
                if j in matches:
                    matches.remove(j)
                    pending.append(j)
                    component.append(j)
        xs, ys = [i % width for i in component], [i // width for i in component]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        w, h = right - left + 1, bottom - top + 1
        if 12 <= w <= 160 and 12 <= h <= 160 and len(component) / (w * h) > .95:
            candidates.append(((left + right + 1) / 2, (top + bottom + 1) / 2))
    if len(candidates) != 1:
        raise ProbeError(f"marker: expected one {color} rectangle, found {len(candidates)}")
    return candidates[0]


def axis_mapping(dom, screen):
    """Fit independent positive scales and translation using 3 non-collinear points."""
    if len(dom) != 3 or len(screen) != 3:
        raise ProbeError("mapping: exactly three markers required")
    area = ((dom[1][0] - dom[0][0]) * (dom[2][1] - dom[0][1])
            - (dom[2][0] - dom[0][0]) * (dom[1][1] - dom[0][1]))
    if abs(area) < 1:
        raise ProbeError("mapping: collinear markers")
    result = []
    for axis in (0, 1):
        d, s = [p[axis] for p in dom], [p[axis] for p in screen]
        dm, sm = sum(d) / 3, sum(s) / 3
        variance = sum((v - dm) ** 2 for v in d)
        scale = sum((a - dm) * (b - sm) for a, b in zip(d, s, strict=True)) / variance
        offset = sm - scale * dm
        if not math.isfinite(scale) or scale <= 0 or any(
                abs(scale * a + offset - b) > 2 for a, b in zip(d, s, strict=True)):
            raise ProbeError("mapping: inconsistent or reflected marker geometry")
        result.extend((scale, offset))
    return tuple(result)


def to_quartz(point, mapping, pixels, bounds):
    sx, ox, sy, oy = mapping
    px, py = point[0] * sx + ox, point[1] * sy + oy
    width, height = pixels
    bx, by, bw, bh = bounds
    if min(width, height, bw, bh) <= 0 or not (0 <= px < width and 0 <= py < height):
        raise ProbeError("mapping: input outside captured main display")
    return bx + px * bw / width, by + py * bh / height


def prepare_fixture(directory: Path) -> tuple[Path, str]:
    path = directory / "fixture.html"
    path.write_bytes(HTML)
    return path, path.resolve().as_uri()


def parse_devtools_active_port(content: str) -> str:
    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        raise ProbeError("DevToolsActivePort file incomplete")
    try:
        port = int(lines[0])
    except ValueError as e:
        raise ProbeError(f"invalid DevToolsActivePort port: {lines[0]}") from e
    if not (1 <= port <= 65535):
        raise ProbeError(f"DevToolsActivePort port out of range: {port}")
    browser_path = lines[1]
    if not browser_path.startswith("/devtools/browser/"):
        raise ProbeError(f"invalid DevToolsActivePort browser path: {browser_path}")
    uuid_part = browser_path[len("/devtools/browser/"):]
    try:
        browser_id = uuid.UUID(uuid_part)
    except ValueError as e:
        raise ProbeError(f"invalid DevToolsActivePort browser UUID: {browser_path}") from e
    if str(browser_id) != uuid_part.lower():
        raise ProbeError(f"invalid DevToolsActivePort browser UUID: {browser_path}")
    return f"ws://127.0.0.1:{port}{browser_path}"


def read_devtools_active_port(profile: Path) -> str:
    port_file = profile / "DevToolsActivePort"
    if not port_file.is_file():
        raise ProbeError(f"DevToolsActivePort file missing: {port_file}")
    return parse_devtools_active_port(port_file.read_text(encoding="utf-8"))


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "experimental native probe, not macOS MCP support", "stages": [],
              "permissions": {}, "finder": "not_run"}
    stage = "platform"
    process = None
    try:
        require_macos()
        stage = "imports"
        import Quartz as Q
        from AppKit import NSRunningApplication
        from PIL import Image
        from playwright.sync_api import sync_playwright

        report["stages"].append({"stage": stage, "status": "pass"})
        stage = "permissions"
        preflight = getattr(Q, "CGPreflightScreenCaptureAccess", None)
        report["permissions"]["screen_capture"] = bool(preflight()) if preflight else None
        framework = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        trusted = getattr(framework, "AXIsProcessTrusted", None)
        if trusted:
            trusted.restype = ctypes.c_bool
        report["permissions"]["accessibility"] = bool(trusted()) if trusted else None
        denied = [name for name, value in report["permissions"].items() if value is False]
        if denied:
            raise ProbeError(f"TCC denied {', '.join(denied)} for probe process; no prompts automated")
        report["stages"].append({"stage": stage, "status": "observed",
                                  "note": "unavailable APIs remain null; real actions still required"})
        stage = "display"
        error, _displays, count = Q.CGGetActiveDisplayList(16, None, None)
        if error or count != 1:
            raise ProbeError("requires exactly one active display; refusing ambiguous desktop mapping")
        rect = Q.CGDisplayBounds(Q.CGMainDisplayID())
        bounds = (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
        if bounds[:2] != (0, 0):
            raise ProbeError("unexpected main display origin")
        report["stages"].append({"stage": stage, "status": "pass"})
        stage = "chrome_launch"
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.is_file():
            raise ProbeError("runner Google Chrome executable missing")
        with tempfile.TemporaryDirectory(prefix="macos-probe-fixture-") as fixture_dir, \
                tempfile.TemporaryDirectory(prefix="macos-probe-profile-") as profile:
            _fixture_path, url = prepare_fixture(Path(fixture_dir))
            process = subprocess.Popen([str(chrome), f"--user-data-dir={profile}",
                                        "--remote-debugging-address=127.0.0.1",
                                        "--remote-debugging-port=0", "--no-first-run",
                                        "--no-default-browser-check", url],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                with sync_playwright() as pw:
                    browser = None
                    for _ in range(60):
                        if process.poll() is not None:
                            raise ProbeError("own Chrome process exited before CDP readiness")
                        try:
                            endpoint = read_devtools_active_port(Path(profile))
                            browser = pw.chromium.connect_over_cdp(endpoint, timeout=1000)
                            break
                        except Exception:
                            time.sleep(.5)
                    if browser is None:
                        raise ProbeError("CDP readiness timeout")
                    page = next((p for c in browser.contexts for p in c.pages if p.url == url), None)
                    if page is None:
                        raise ProbeError("own local test tab missing")
                    page.wait_for_selector("#a")
                    report["viewport"] = page.evaluate(
                        "({width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio})")
                    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
                    if app is None:
                        raise ProbeError("cannot resolve own Chrome application")
                    report["stages"].append({"stage": stage, "status": "pass"})

                    def foreground():
                        if not app.activateWithOptions_(2):  # NSApplicationActivateIgnoringOtherApps
                            raise ProbeError("Chrome activation failed")
                        time.sleep(.2)
                        if not app.isActive():
                            raise ProbeError("Chrome is not foreground")

                    def capture(name):
                        path = output / name
                        subprocess.run(["/usr/sbin/screencapture", "-x", "-D", "1", str(path)],
                                       check=True, timeout=15)
                        with Image.open(path) as image:
                            image = image.convert("RGB")
                            size = image.size
                            pixels = image.get_flattened_data()
                            points = [find_marker(pixels, *size, color)
                                      for color in MARKERS.values()]
                        boxes = [page.locator(f"#{key}").bounding_box() for key in MARKERS]
                        if any(b is None for b in boxes):
                            raise ProbeError("test marker DOM box missing")
                        dom = [(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                               for b in boxes]
                        return axis_mapping(dom, points), size

                    def location(selector, mapping, size):
                        b = page.locator(selector).bounding_box()
                        if b is None:
                            raise ProbeError(f"missing target {selector}")
                        return to_quartz((b["x"] + b["width"] / 2,
                                          b["y"] + b["height"] / 2), mapping, size, bounds)

                    def mouse(kind, point):
                        event = Q.CGEventCreateMouseEvent(None, kind, point, Q.kCGMouseButtonLeft)
                        Q.CGEventPost(Q.kCGHIDEventTap, event)

                    def click(point):
                        mouse(Q.kCGEventMouseMoved, point)
                        mouse(Q.kCGEventLeftMouseDown, point)
                        mouse(Q.kCGEventLeftMouseUp, point)

                    def command(code):
                        for down in (True, False):
                            event = Q.CGEventCreateKeyboardEvent(None, code, down)
                            Q.CGEventSetFlags(event, Q.kCGEventFlagMaskCommand)
                            Q.CGEventPost(Q.kCGHIDEventTap, event)

                    def paste(text):
                        subprocess.run(["/usr/bin/pbcopy"], input=text.encode("utf-8"),
                                       env={**os.environ, "LC_CTYPE": "en_US.UTF-8"},
                                       check=True, timeout=5)
                        command(9)  # ANSI V

                    def check(expression):
                        page.wait_for_function(expression, timeout=5000)
                        report["stages"].append({"stage": stage, "status": "pass"})

                    stage = "screenshot_before_mapping"
                    foreground()
                    mapping, size = capture("before.png")
                    report["mapping"] = {"dom_to_pixels": mapping, "pixels": size,
                                         "quartz_bounds": bounds}
                    report["stages"].append({"stage": stage, "status": "pass"})
                    stage = "native_click"
                    foreground()
                    click(location("#click", mapping, size))
                    check("probe.clicks === 1")
                    stage = "native_chinese_paste"
                    foreground()
                    click(location("#text", mapping, size))
                    paste("原生桌面中文输入")
                    check("input.value === '原生桌面中文输入' && "
                          "probe.inputs.includes('原生桌面中文输入')")
                    stage = "native_keyboard_replace"
                    foreground()
                    command(0)  # ANSI A
                    page.wait_for_function(
                        "input.selectionStart === 0 && input.selectionEnd === input.value.length",
                        timeout=5000)
                    paste("中文替换成功")
                    check("input.value === '中文替换成功' && "
                          "probe.keys.some(e => e.meta && e.key.toLowerCase() === 'a')")
                    stage = "native_scroll"
                    foreground()
                    mouse(Q.kCGEventMouseMoved, location("#scroll", mapping, size))
                    event = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitPixel, 1, -180)
                    Q.CGEventPost(Q.kCGHIDEventTap, event)
                    check("probe.scroll > 0 && document.querySelector('#scroll').scrollTop > 0")
                    stage = "native_drag"
                    foreground()
                    start, end = location("#drag", mapping, size), location("#drop", mapping, size)
                    mouse(Q.kCGEventMouseMoved, start)
                    mouse(Q.kCGEventLeftMouseDown, start)
                    try:
                        for n in range(1, 21):
                            point = tuple(a + (b - a) * n / 20 for a, b in zip(start, end, strict=True))
                            mouse(Q.kCGEventLeftMouseDragged, point)
                            time.sleep(.03)
                    finally:
                        mouse(Q.kCGEventLeftMouseUp, end)
                    check("probe.drag === true && probe.dragMoves > 0")
                    stage = "screenshot_after_mapping"
                    foreground()
                    capture("after.png")
                    report["stages"].append({"stage": stage, "status": "pass"})
                    report["dom"] = page.evaluate("probe")
                    stage = "finder_manual_evidence"
                    subprocess.run(["/usr/bin/open", "-a", "Finder", profile], check=True, timeout=10)
                    time.sleep(1)
                    subprocess.run(["/usr/sbin/screencapture", "-x", "-D", "1",
                                    str(output / "finder.png")], check=True, timeout=15)
                    report["finder"] = "manual_review_required; not an automatic PASS"
                    report["stages"].append({"stage": stage, "status": "manual_review_required"})
            finally:
                stop_process(process)
                process = None
        report["result"] = "native_probe_pass; Finder still requires manual review"
        return 0
    except Exception as exc:
        report["stages"].append({"stage": stage, "status": "fail",
                                  "reason": str(exc)[:1500]})
        report["result"] = "fail"
        print(f"macOS probe failed at {stage}: {exc}", file=sys.stderr)
        return 1
    finally:
        stop_process(process)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("macos-probe-artifacts"))
    sys.exit(run(parser.parse_args().output))
