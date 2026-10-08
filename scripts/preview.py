#!/usr/bin/env python3
"""Bounded, single-owner development preview. Never publish tokens or user data."""

import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

SUPPORTED_MODES = {"preview", "verify"}


def public_origin(value):
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("public_url must not contain whitespace or control characters")
    value = value.rstrip("/")
    url = urlsplit(value)
    if (
        url.scheme != "https" or not url.hostname or url.username or url.password
        or url.path or url.query or url.fragment or url.port not in {None, 443}
        or not re.fullmatch(r"[a-zA-Z0-9.-]+", url.hostname)
        or "." not in url.hostname or url.hostname in {"127.0.0.1", "localhost"}
    ):
        raise ValueError("public_url must be an HTTPS origin, without credentials or a path")
    return value


def configuration(env):
    if env.get("PREVIEW_PLATFORM", "linux") not in {"linux", "macos"}:
        raise ValueError("PREVIEW_PLATFORM must be linux or macos")
    mode = env.get("PREVIEW_MODE", "preview")
    kind = env.get("TUNNEL_KIND", "quick")
    minutes = int(env.get("PREVIEW_MINUTES", "60"))
    if mode not in SUPPORTED_MODES or kind not in {"quick", "named"}:
        raise ValueError("Unsupported preview mode or tunnel kind")
    if not 10 <= minutes <= 350:
        raise ValueError("Preview lifetime must be 10–350 minutes")
    owner = env.get("BRIDGE_OWNER_TOKEN", "")
    if mode == "preview" and (
        not 8 <= len(owner) <= 256 or not owner.strip()
    ):
        raise ValueError("Set repository Actions secret BRIDGE_OWNER_TOKEN (8–256 characters; not blank)")
    base = ""
    if kind == "named":
        base = public_origin(env.get("PREVIEW_PUBLIC_URL", ""))
        if not env.get("TUNNEL_TOKEN"):
            raise ValueError("Named tunnel requires Actions secret CLOUDFLARE_TUNNEL_TOKEN")
    return mode, kind, minutes, base


def summary(message):
    print(message, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(message + "\n\n")


class PreviewDesktop:
    """Own only this preview's container or native processes and temporary files."""

    def __init__(self, platform):
        self.platform = platform
        self.chrome = self.service = self.directory = None

    def start(self, env):
        self.env = env
        if self.platform == "linux":
            subprocess.run(
                ["docker", "run", "-d", "--name", "bridge-preview", "--shm-size=1g",
                 "--memory=3g", "--cpus=2", "--pids-limit=512", "-p", "127.0.0.1:8080:8080",
                 "-e", "BRIDGE_OWNER_TOKEN", "-e", "BRIDGE_PUBLIC_URL", "desktop-bridge:preview"],
                env=env, check=True, stdout=subprocess.DEVNULL)
            return
        if sys.platform != "darwin":
            raise RuntimeError("macOS preview requires a macOS runner")
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.is_file():
            raise RuntimeError("Runner Google Chrome executable missing")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 8080))
        self.directory = tempfile.TemporaryDirectory(prefix="bridge-macos-")
        root = Path(self.directory.name)
        # Service processes do not inherit runner credentials or tunnel tokens.
        self.env = {k: env[k] for k in ("PATH", "HOME", "LANG", "TMPDIR",
                                       "BRIDGE_OWNER_TOKEN", "BRIDGE_PUBLIC_URL") if k in env}
        self.env.update(BRIDGE_DESKTOP_BACKEND="macos", BRIDGE_BIND="127.0.0.1",
                        BRIDGE_DATA=str(root / "data"))
        self.chrome = subprocess.Popen(
            [str(chrome), f"--user-data-dir={root / 'chrome'}",
             "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from macos_probe import ProbeError, read_devtools_active_port

        for _ in range(60):
            if self.chrome.poll() is not None:
                raise RuntimeError("Own Chrome process exited before CDP readiness")
            try:
                self.env["BRIDGE_CDP_ENDPOINT"] = read_devtools_active_port(root / "chrome")
                break
            except ProbeError:
                time.sleep(.5)
        else:
            raise RuntimeError("Own Chrome DevToolsActivePort did not become ready")
        self._start_service()

    def _start_service(self):
        self.service = subprocess.Popen([sys.executable, "-m", "desktop_bridge.app"],
                                        env=self.env)

    def check(self):
        if self.platform == "macos" and any(
            process is None or process.poll() is not None for process in (self.chrome, self.service)
        ):
            raise RuntimeError("Native macOS Chrome or MCP service exited")

    def restart(self):
        if self.platform == "linux":
            subprocess.run(["docker", "restart", "bridge-preview"], check=True,
                           stdout=subprocess.DEVNULL)
        else:
            self.stop_process(self.service)
            self._start_service()

    @staticmethod
    def stop_process(process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def close(self):
        if self.platform == "linux":
            subprocess.run(["docker", "rm", "-fv", "bridge-preview"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.stop_process(self.service)
            self.stop_process(self.chrome)
            if self.directory:
                self.directory.cleanup()


def main():
    mode, kind, minutes, base = configuration(os.environ)
    if "--check" in sys.argv:
        print("Preview configuration is valid. No secrets printed.")
        return
    import httpx
    from tunnel_smoke import smoke

    # Verify runs use fresh, disposable test credentials. Interactive runs require
    # the owner's preconfigured repository secret, never a public workflow input.
    owner = secrets.token_urlsafe(40) if mode == "verify" else os.environ["BRIDGE_OWNER_TOKEN"]
    print(f"::add-mask::{owner}", flush=True)
    if os.environ.get("TUNNEL_TOKEN"):
        print(f"::add-mask::{os.environ['TUNNEL_TOKEN']}", flush=True)
    cloudflared = str(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "cloudflared")
    log = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "bridge-tunnel.log"
    args = [cloudflared, "tunnel", "--no-autoupdate", "--protocol", "http2"]
    args += ["--url", "http://127.0.0.1:8080"] if kind == "quick" else ["run"]
    tunnel_env = {k: os.environ[k] for k in ("PATH", "HOME", "TUNNEL_TOKEN") if k in os.environ}
    process = None
    desktop = PreviewDesktop(os.environ.get("PREVIEW_PLATFORM", "linux"))

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with log.open("w") as output:
            process = subprocess.Popen(args, stdout=output, stderr=output, env=tunnel_env)
        if kind == "quick":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel exited before publishing an address")
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text())
                if match:
                    base = public_origin(match.group())
                    break
                time.sleep(1)
            if not base:
                raise RuntimeError("Cloudflare did not allocate a URL within 90 seconds")
        env = {**os.environ, "BRIDGE_OWNER_TOKEN": owner, "BRIDGE_PUBLIC_URL": base}
        desktop.start(env)
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                desktop.check()
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel stopped")
                try:
                    response = client.get(base + "/healthz")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(3)
            else:
                raise RuntimeError("Public HTTPS readiness failed; check named hostname routing to http://127.0.0.1:8080")
        # A working health check is insufficient. Verify discovery, PKCE, JSON MCP,
        # actual tools and authenticated desktop WebSocket across the public tunnel.
        smoke(base, owner, native_pid=desktop.chrome.pid if desktop.chrome else None,
              native_endpoint=desktop.env.get("BRIDGE_CDP_ENDPOINT"))
        if mode == "verify":
            summary("Public Cloudflare HTTPS smoke test passed. Disposable test session is now shutting down; this is not a user login URL.")
            return
        # Clear test registrations and restore READY so the first real client
        # can start without inheriting the smoke test's paused state.
        desktop.restart()
        with httpx.Client(timeout=10) as client:
            for _ in range(60):
                desktop.check()
                try:
                    response = client.get(base + "/healthz")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("Desktop failed readiness after clearing test state")
        expiry = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() + minutes * 60))
        summary(f"## Agent Computer development preview ({desktop.platform})\n\nMCP endpoint: {base}/mcp\n\nDesktop and OAuth login: {base}\n\nAuthentication: OAuth with dynamic client registration and PKCE. Sign in using your BRIDGE_OWNER_TOKEN; never paste it into ChatGPT.\n\nScheduled stop: {expiry}. Cancel this workflow to stop early. Download needed files before stopping: the desktop and its data are disposable.\n\nQuick URLs change on restart. This session is for developing and testing Agent Computer, not permanent hosting.")
        deadline = time.monotonic() + minutes * 60
        failures = 0
        with httpx.Client(timeout=10) as client:
            while time.monotonic() < deadline:
                desktop.check()
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel exited; start a new preview")
                try:
                    failures = 0 if client.get("http://127.0.0.1:8080/healthz").status_code == 200 else failures + 1
                except httpx.HTTPError:
                    failures += 1
                if failures >= 3:
                    raise RuntimeError("Desktop health check failed three times")
                time.sleep(min(15, max(0, deadline - time.monotonic())))
        summary("Preview lifetime ended. The tunnel, desktop and disposable files have been removed.")
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        desktop.close()
        log.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
