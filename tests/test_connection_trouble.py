"""connection_hint/add_connection_hints: turning a dead backend into a directive message.

The raw transport text invites exactly the wrong response from an AI caller -
hunting for a PID, an old port, a coredump to explain a crash. These tests pin
what each backend shape (managed / fixed standalone / auto-discovery) is told
to do instead, and that everything else (Busy, a genuine backend exception)
passes through untouched.
"""

from __future__ import annotations

from iar_cspy_mcp._connection_trouble import add_connection_hints, connection_hint

REFUSED = "Thrift RPC failed: TTransportException: Could not connect to ('127.0.0.1', 40557)"
RESET = "Thrift RPC failed: TTransportException: WinError 10054 connection reset"
NEVER_FOUND_A = "No service registry available right now for service 'debugger'"
NEVER_FOUND_B = "A service registry (registry_uri, THRIFT_REGISTRY_URI) is required to query services"
NEVER_FOUND_C = "no registry configured (registry_uri, auto-discovery) and no direct debugger endpoint override"
NOT_A_CONNECTION_FAULT = "Thrift RPC failed: CSpyException kDcFatalError (3) method=go message=Session aborted!"
BUSY = "projectmanager.BuildProject: a build or analysis is in progress"


def test_a_backend_that_was_never_found_says_do_not_search():
    hint = connection_hint(NEVER_FOUND_A, mode="standalone", registry_uri=None)
    assert hint is not None
    assert "never been one found" in hint
    assert "do not" in hint.lower()


def test_both_never_found_message_shapes_are_recognised():
    """Client.endpoint()'s NotConnectedError and ServiceRegistry._endpoint()'s
    plain CSpyError say this in unrelated words; both must be caught."""
    for text in (NEVER_FOUND_A, NEVER_FOUND_B, NEVER_FOUND_C):
        assert connection_hint(text, mode="standalone", registry_uri=None) is not None


def test_auto_discovery_mode_is_told_to_wait_not_search():
    hint = connection_hint(REFUSED, mode="standalone", registry_uri=None)
    assert hint is not None
    assert "PID" in hint
    assert "wait" in hint.lower()
    assert "coredump" in hint.lower()


def test_fixed_registry_mode_is_told_to_ask_the_user_not_search():
    hint = connection_hint(REFUSED, mode="standalone", registry_uri="thrift://10.0.0.5:4711")
    assert hint is not None
    assert "10.0.0.5:4711" in hint
    assert "ask the user" in hint.lower()


def test_managed_mode_is_told_it_self_heals():
    hint = connection_hint(RESET, mode="managed", registry_uri=None)
    assert hint is not None
    assert "restarts it automatically" in hint
    assert "PID" in hint


def test_an_unrelated_backend_fault_is_left_alone():
    """A real CSpyException (e.g. a fatal session error) is not a dead backend."""
    assert connection_hint(NOT_A_CONNECTION_FAULT, mode="standalone", registry_uri=None) is None


def test_busy_is_left_alone():
    assert connection_hint(BUSY, mode="standalone", registry_uri=None) is None


def test_add_connection_hints_only_touches_known_fields_and_keeps_the_original():
    data = {
        "backend_online_error": REFUSED,
        "core_state_error": "some unrelated string",
        "registry_error": NEVER_FOUND_B,
        "unrelated_field": REFUSED,  # not a recognised field: must not get a hint
    }
    out = add_connection_hints(dict(data), mode="standalone", registry_uri=None)
    assert out["backend_online_error"] == REFUSED  # original untouched
    assert "backend_online_error_hint" in out
    assert "registry_error_hint" in out
    assert "core_state_error_hint" not in out  # didn't match anything
    assert "unrelated_field_hint" not in out
