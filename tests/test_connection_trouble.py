"""A missing or dead backend: iar_cspy classifies it, this server tells the caller what to do.

The classification itself (``BackendUnavailableError.kind``) is tested in
cspy-py; these tests pin the directive text per kind, and that every tool and
status snapshot carries it. Real Clients against dead or absent sockets.
"""

from __future__ import annotations

import socket

import pytest

from iar_cspy import BackendUnavailableError, Client, Config, CSpyError
from iar_cspy.errors import DISCOVERED_GONE, FIXED_UNREACHABLE, MANAGED_EXITED, NEVER_FOUND, BusyError
from iar_cspy.testing import project_manager_error
from iar_cspy_mcp._connection_trouble import add_connection_guidance, guidance


@pytest.fixture()
def dead_registry(server):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    uri = f"thrift://127.0.0.1:{probe.getsockname()[1]}"
    probe.close()  # nothing listens here now
    client = Client(Config(mode="standalone", registry_uri=uri), owns_backend=True)
    server.set_client(client)
    yield uri
    server.set_client(None)
    client.close()


@pytest.fixture()
def no_backend(server, tmp_path):
    client = Client(Config(mode="standalone", auto_discovery_dir=tmp_path), owns_backend=True)
    server.set_client(client)
    yield
    server.set_client(None)
    client.close()


@pytest.mark.parametrize("kind", [NEVER_FOUND, MANAGED_EXITED, FIXED_UNREACHABLE, DISCOVERED_GONE])
def test_every_kind_says_not_to_go_searching(kind):
    text = guidance(kind, registry_uri="thrift://10.0.0.5:4711")
    assert text is not None
    assert "do not" in text.lower()


def test_each_kind_gets_its_own_next_step():
    assert "retry" in guidance(MANAGED_EXITED).lower() and "restarts it automatically" in guidance(MANAGED_EXITED)
    assert "ask the user" in guidance(FIXED_UNREACHABLE, registry_uri="thrift://h:1").lower()
    assert "thrift://h:1" in guidance(FIXED_UNREACHABLE, registry_uri="thrift://h:1")
    assert "wait" in guidance(DISCOVERED_GONE).lower()
    assert "Start an IAR IDE" in guidance(NEVER_FOUND)


def test_an_unknown_kind_has_no_guidance():
    assert guidance("something_new") is None


def test_a_tool_on_a_dead_fixed_registry_gets_the_directive_text(server, dead_registry):
    with pytest.raises(BackendUnavailableError) as exc_info:
        server.debugger_is_online()
    exc = exc_info.value
    assert exc.kind == FIXED_UNREACHABLE
    message = str(exc)
    assert "Could not connect" in message  # the raw reason is kept
    assert dead_registry in message and "ask the user" in message.lower()
    assert message.count("Do not search") == 1


def test_a_tool_with_no_backend_found_gets_the_directive_text(server, no_backend):
    with pytest.raises(BackendUnavailableError, match="Start an IAR IDE") as exc_info:
        server.debugger_is_online()
    assert exc_info.value.kind == NEVER_FOUND


def test_project_tools_get_it_too(server, dead_registry):
    with pytest.raises(BackendUnavailableError, match="ask the user"):
        server.project_status()


def test_busy_passes_through_unchanged(server, fake):

    _, rpc = fake
    rpc.on("projectmanager", "BuildProject", project_manager_error(description="a build is already running"))
    with pytest.raises(BusyError, match="a build is already running") as exc_info:
        server.projectmanager_call("BuildProject", '[{"filename": "/tmp/p.ewp"}, "Debug", 4]')
    assert "Do not" not in str(exc_info.value)


def test_other_errors_pass_through_unchanged(server, fake):
    from iar_cspy.testing import cspy_exception

    _, rpc = fake
    rpc.on("debugger", "isOnline", cspy_exception(code=3, method="isOnline", message="Session aborted!"))
    with pytest.raises(CSpyError) as exc_info:
        server.debugger_is_online()
    assert not isinstance(exc_info.value, BackendUnavailableError)


def test_session_status_keeps_the_raw_error_and_gets_directive_text(server, dead_registry):
    data = server.debugger_session_status()["data"]
    assert "Could not connect" in data["backend_online_error"]
    assert data["backend_online_error_kind"] == FIXED_UNREACHABLE
    assert dead_registry in data["backend_online_error_hint"]
    assert "ask the user" in data["backend_online_error_hint"].lower()


def test_ide_services_status_gets_directive_text(server, no_backend):
    data = server.ide_services_status()["data"]
    assert "registry_error" in data
    assert data["registry_error_kind"] == NEVER_FOUND
    assert "Start an IAR IDE" in data["registry_error_hint"]


def test_session_status_with_a_working_backend_has_no_hint(server, fake):
    data = server.debugger_session_status()["data"]
    assert not [key for key in data if key.endswith("_hint")]


def test_add_connection_guidance_only_touches_fields_with_a_known_kind():
    data = {
        "backend_online_error": "x",
        "backend_online_error_kind": NEVER_FOUND,
        "backend_online_error_hint": "short",
        "core_state_error": "y",  # no kind: not a missing backend
        "unrelated_kind": NEVER_FOUND,
    }
    out = add_connection_guidance(dict(data), registry_uri=None)
    assert out["backend_online_error_hint"] != "short"
    assert out["backend_online_error"] == "x"
    assert "core_state_error_hint" not in out
    assert "unrelated_hint" not in out
