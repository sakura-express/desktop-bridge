import pytest
from test_auth import grant

from desktop_bridge.auth import Auth, digest
from desktop_bridge.state import BridgeError

TOKEN = "test-only-owner-token-not-for-deployment-123"


def test_tickets_are_single_use_grant_bound_and_not_owner_credentials():
    auth = Auth(TOKEN, "https://bridge.example")
    token = auth.exchange(grant(auth))["access_token"]
    value, deadline = auth.viewer_ticket(token)
    assert token not in repr(auth.viewer_tickets)
    assert value not in repr(auth.viewer_tickets)
    sid, expires = auth.redeem_viewer_ticket(value)
    assert deadline <= expires == auth.tokens[digest(token)]
    assert auth.viewer_session(sid)
    assert auth.session(sid) is None
    assert not auth.bearer(sid)
    with pytest.raises(BridgeError):
        auth.check_csrf(sid, "any")
    for invalid in [value, "wrong", None, [], "x" * 44]:
        with pytest.raises(BridgeError):
            auth.redeem_viewer_ticket(invalid)
    with pytest.raises(BridgeError):
        auth.viewer_ticket(TOKEN)
    auth.tokens.pop(digest(token))
    assert auth.viewer_session(sid) is None


def test_ticket_expiry_revoke_and_bounded_storage(monkeypatch):
    import desktop_bridge.auth as auth_module

    clock = [1000.0]
    monkeypatch.setattr(auth_module.time, "time", lambda: clock[0])
    auth = Auth(TOKEN, "https://bridge.example")
    token = auth.exchange(grant(auth))["access_token"]
    expired, _ = auth.viewer_ticket(token)
    clock[0] += 60
    with pytest.raises(BridgeError):
        auth.redeem_viewer_ticket(expired)
    for _ in range(128):
        auth.viewer_ticket(token)
    with pytest.raises(BridgeError):
        auth.viewer_ticket(token)
    clock[0] += 61
    value, _ = auth.viewer_ticket(token)
    assert len(auth.viewer_tickets) == 1
    sid, _ = auth.redeem_viewer_ticket(value)
    pending, _ = auth.viewer_ticket(token)
    clock[0] = auth.tokens[digest(token)]
    assert auth.viewer_session(sid) is None
    with pytest.raises(BridgeError):
        auth.redeem_viewer_ticket(pending)
    auth.revoke()
    assert not auth.viewer_sessions and not auth.viewer_tickets


def test_pending_ticket_revoked_and_restart_does_not_restore_access():
    auth = Auth(TOKEN, "https://bridge.example")
    token = auth.exchange(grant(auth))["access_token"]
    pending, _ = auth.viewer_ticket(token)
    second, _ = auth.viewer_ticket(token)
    sid, _ = auth.redeem_viewer_ticket(second)
    restarted = Auth(TOKEN, "https://bridge.example")
    assert restarted.viewer_session(sid) is None
    with pytest.raises(BridgeError):
        restarted.redeem_viewer_ticket(pending)
    auth.revoke()
    assert auth.viewer_session(sid) is None
    with pytest.raises(BridgeError):
        auth.redeem_viewer_ticket(pending)


def test_viewer_session_count_is_bounded_and_expired_entries_are_pruned():
    auth = Auth(TOKEN, "https://bridge.example")
    token = auth.exchange(grant(auth))["access_token"]
    for _ in range(128):
        pending, _ = auth.viewer_ticket(token)
        auth.redeem_viewer_ticket(pending)
    pending, _ = auth.viewer_ticket(token)
    with pytest.raises(BridgeError):
        auth.redeem_viewer_ticket(pending)
    auth.tokens.pop(digest(token))
    next_token = auth.exchange(grant(auth))["access_token"]
    pending, _ = auth.viewer_ticket(next_token)
    sid, _ = auth.redeem_viewer_ticket(pending)
    assert len(auth.viewer_sessions) == 1 and auth.viewer_session(sid)
