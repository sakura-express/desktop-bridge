"""Preflight an unlocked single-display Windows desktop without injecting input."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Windows desktop availability; actual input verified through public MCP",
              "result": "fail"}
    try:
        from desktop_bridge.windows import WindowsDesktop

        desktop = WindowsDesktop()
        (output / "desktop.png").write_bytes(asyncio.run(desktop.screenshot()))
        report.update(result="pass", pixels=desktop.size, desktop="unlocked Default",
                      input="not_tested; public MCP smoke must pass before preview readiness")
        return 0
    except Exception as error:
        report["reason"] = str(error)[:1500]
        return 1
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("windows-probe-artifacts"))
    raise SystemExit(run(parser.parse_args().output))
