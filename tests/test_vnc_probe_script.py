"""Static AST and structural regression check for scripts/e2e.py VNC probe timing."""

from __future__ import annotations

import ast
from pathlib import Path


def test_vnc_probe_script_structure():
    e2e_path = Path(__file__).resolve().parents[1] / "scripts" / "e2e.py"
    content = e2e_path.read_text(encoding="utf-8")

    # 1. Must parse as valid Python syntax
    tree = ast.parse(content, filename=str(e2e_path))

    # 2. Check probe asynccontextmanager definition
    found_probe = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "probe":
            decorators = [
                d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
                for d in node.decorator_list
            ]
            assert "asynccontextmanager" in decorators, f"probe must be asynccontextmanager: {decorators}"
            found_probe = True
            break
    assert found_probe, "probe helper not found in scripts/e2e.py"

    # 3. Dedicated handle window.__bridgeVncProbe must be used
    assert "window.__bridgeVncProbe" in content

    # 4. Must not use CDP type/fill/press to forge input on remote #note
    # Locate setup_remote_note_focus in content
    start = content.find("async def setup_remote_note_focus():")
    assert start != -1
    end = content.find("async def send_probe_key():", start)
    setup_code = content[start:end]
    assert ".type(" not in setup_code, "CDP type must not be used for setup"
    assert ".fill(" not in setup_code, "CDP fill must not be used for setup"
    assert ".press(" not in setup_code, "CDP press must not be used for setup"
    assert 'value = "VIEW_ONLYz"' not in setup_code and "value = 'VIEW_ONLYz'" not in setup_code

    # 5. Must send 0x7a ('KeyZ') and disallow re-sending
    assert "0x7a" in content
    assert "KeyZ" in content
    assert "re-sending not allowed" in content

    # 6. Must have 5s timeout on connect and wait_for_function
    assert "5000" in content
    assert "VIEW_ONLYz" in content
    assert "VIEW_ONLY" in content
