"""Interactive public MCP diagnostics. Credentials stay in process memory.

JSON lines on stdin: login {base, owner}; call {name, arguments}; tools; exit.
No logout endpoint is called: bridge logout revokes other clients' grants too.
"""
import base64
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

http = None
headers = {}
counter = 0
observation = None
output = Path(__file__).resolve().parents[1] / "probe-output"
run_id = secrets.token_hex(4)


def rpc(method, params):
    global counter
    counter += 1
    started = time.monotonic()
    response = http.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": counter, "method": method, "params": params})
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(data["error"])
    return data["result"], round((time.monotonic() - started) * 1000)


print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    try:
        command = json.loads(line)
        if command["op"] == "exit":
            break
        if command["op"] == "login":
            base = command["base"].rstrip("/")
            http = httpx.Client(base_url=base, timeout=35, follow_redirects=False)
            health = http.get("/healthz")
            print(json.dumps({"health_status": health.status_code,
                              "health": health.json() if health.status_code == 200 else None}), flush=True)
            response = http.post("/api/login", json={"token": command.pop("owner")})
            response.raise_for_status()
            csrf = response.json()["csrf"]
            initial = http.get("/api/status")
            initial.raise_for_status()
            state = initial.json()
            print(json.dumps({"initial_state": state.get("state"),
                              "resolution": state.get("resolution"),
                              "desktop_platform": state.get("desktop_platform")}), flush=True)
            registered = http.post("/register", json={"client_name": "NekoCode diagnostic probe",
                "redirect_uris": ["http://127.0.0.1:43111/callback"],
                "token_endpoint_auth_method": "none"})
            registered.raise_for_status()
            client = registered.json()
            verifier = secrets.token_urlsafe(48)
            params = {"client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
                "response_type": "code", "code_challenge_method": "S256",
                "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
                "resource": base + "/mcp", "scope": "computer", "state": "diagnostic-probe"}
            approval = http.post("/authorize", data={**params, "csrf": csrf})
            if approval.status_code != 303:
                raise ValueError(f"OAuth approval HTTP {approval.status_code}")
            code = parse_qs(urlsplit(approval.headers["location"]).query)["code"][0]
            token = http.post("/token", data={"grant_type": "authorization_code", "code": code,
                "client_id": client["client_id"], "redirect_uri": params["redirect_uri"],
                "resource": base + "/mcp", "code_verifier": verifier})
            token.raise_for_status()
            headers = {"Authorization": "Bearer " + token.json()["access_token"],
                       "Accept": "application/json, text/event-stream"}
            initialized, elapsed = rpc("initialize", {"protocolVersion": "2025-03-26",
                "capabilities": {}, "clientInfo": {"name": "NekoCode-diagnostic", "version": "1"}})
            headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
            print(json.dumps({"initialized": initialized["serverInfo"], "elapsed_ms": elapsed}), flush=True)
        elif command["op"] == "tools":
            result, elapsed = rpc("tools/list", {})
            print(json.dumps({"tools": result["tools"], "elapsed_ms": elapsed}, ensure_ascii=False), flush=True)
        elif command["op"] == "call":
            name = command["name"]
            args = command.get("arguments", {})
            if command.get("use_observation"):
                args["observation_id"] = observation
            if name in {"desktop_action", "browser_action"}:
                args.setdefault("action_id", "probe-" + secrets.token_hex(12))
            result, elapsed = rpc("tools/call", {"name": name, "arguments": args})
            summaries = []
            for part in result.get("content", []):
                if part["type"] == "image":
                    output.mkdir(exist_ok=True)
                    image = output / f"{run_id}-screen-{counter}.png"
                    data = base64.b64decode(part["data"])
                    image.write_bytes(data)
                    summaries.append({"type": "image", "path": str(image), "bytes": len(data)})
                elif part["type"] == "text":
                    try:
                        value = json.loads(part["text"])
                        if isinstance(value, dict) and "observation_id" in value:
                            observation = value["observation_id"]
                    except ValueError:
                        value = part["text"]
                    summaries.append({"type": "text", "value": value})
            print(json.dumps({"tool": name, "isError": result.get("isError", False),
                              "elapsed_ms": elapsed, "content": summaries}, ensure_ascii=False), flush=True)
    except Exception as error:
        # Do not print request headers, input commands, credentials or response cookies.
        print(json.dumps({"error": type(error).__name__, "message": str(error)[:500]}), flush=True)
if http:
    http.close()
