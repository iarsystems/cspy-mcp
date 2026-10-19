from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_callback_registration_and_registry_listing_follow_delayed_registry_file(
    server_module, monkeypatch, tmp_path
):
    registry_file = tmp_path / "RemoteControlServiceLocation.json"
    monkeypatch.setenv("THRIFT_CSPYSERVER_MODE", "standalone")
    monkeypatch.setenv("THRIFT_REGISTRY_FILE", str(registry_file))
    cfg = server_module.load_config()
    monkeypatch.setattr(server_module, "load_config", lambda: cfg)

    server_module._EVENTHANDLER_STARTED = False
    server_module._LIBSUPPORT_STARTED = False
    server_module._EVENTHANDLER_HOST = ""
    server_module._EVENTHANDLER_PORT = 0
    server_module._LIBSUPPORT_HOST = ""
    server_module._LIBSUPPORT_PORT = 0

    registries: dict[int, dict[str, object]] = {}
    registry_connections: list[tuple[str, int]] = []

    class ServiceLocation:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class RegistryClient:
        def __init__(self, host: str, port: int):
            self.host = host
            self.port = port
            registry_connections.append((host, port))

        def getServices(self):
            return registries.setdefault(self.port, {})

        def registerService(self, name, location):
            registries.setdefault(self.port, {})[name] = location

        def close(self):
            pass

    registry_module = SimpleNamespace(
        CSpyServiceRegistry=object,
        ServiceLocation=ServiceLocation,
        Protocol=SimpleNamespace(Binary=0),
        Transport=SimpleNamespace(Socket=0),
    )
    monkeypatch.setattr(
        server_module,
        "load_service_registry_module",
        lambda _include_dirs: registry_module,
    )
    monkeypatch.setattr(
        server_module,
        "make_client",
        lambda _service, host, port, **_kwargs: RegistryClient(host, port),
    )
    monkeypatch.setattr(
        server_module,
        "_find_include_thrift",
        lambda _include_dirs, filename: Path(filename),
    )
    monkeypatch.setattr(
        server_module,
        "load_thrift_module",
        lambda thrift_path, _include_dirs: (
            SimpleNamespace(DebugEventListener=object)
            if str(thrift_path).endswith("cspy.thrift")
            else SimpleNamespace(LibSupportService2=object)
        ),
    )

    class DummyServer:
        daemon = False

        def serve(self):
            pass

    class DummyThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server_module, "make_server", lambda *_args, **_kwargs: DummyServer())
    monkeypatch.setattr(server_module.threading, "Thread", DummyThread)
    monkeypatch.setattr(server_module.time, "sleep", lambda _seconds: None)
    ports = iter((41001, 41002))
    monkeypatch.setattr(server_module, "_pick_free_port", lambda _host: next(ports))
    monkeypatch.setattr(server_module, "_is_tcp_endpoint_reachable", lambda *_args: True)

    for ensure in (
        server_module._ensure_debug_eventhandler,
        server_module._ensure_libsupport,
        lambda: server_module.listwindow_list_services(""),
    ):
        with pytest.raises(server_module.ThriftBridgeError, match="location file not found"):
            ensure()

    registry_file.write_text(
        json.dumps({"1": {"str": "127.0.0.1"}, "2": {"i32": 42001}}),
        encoding="utf-8",
    )
    assert server_module._ensure_debug_eventhandler()["ok"] is True
    assert server_module._ensure_libsupport()["ok"] is True
    first_listing = server_module.listwindow_list_services("")
    assert {entry["name"] for entry in first_listing} == {
        "debugger.eventhandler",
        "libsupport",
    }

    registry_file.write_text(
        json.dumps({"1": {"str": "127.0.0.1"}, "2": {"i32": 42002}}),
        encoding="utf-8",
    )
    assert server_module._ensure_debug_eventhandler()["ok"] is True
    assert server_module._ensure_libsupport()["ok"] is True
    second_listing = server_module.listwindow_list_services("")

    assert {entry["name"] for entry in second_listing} == {
        "debugger.eventhandler",
        "libsupport",
    }
    assert registry_connections
    assert {port for _host, port in registry_connections} == {42001, 42002}
    assert server_module._EVENTHANDLER_PORT == 41001
    assert server_module._LIBSUPPORT_PORT == 41002


def test_listwindow_reconnects_when_endpoint_changes_or_rpc_fails(
    server_module, monkeypatch
):
    server_module._LISTWINDOW_CONNECTED.clear()
    server_module._LISTWINDOW_HOST = "127.0.0.1"
    server_module._LISTWINDOW_PORT = 43001
    monkeypatch.setattr(
        server_module,
        "load_config",
        lambda: SimpleNamespace(timeout_ms=1000, include_dirs=[]),
    )
    monkeypatch.setattr(
        server_module,
        "_find_include_thrift",
        lambda _include_dirs, filename: Path(filename),
    )
    monkeypatch.setattr(
        server_module,
        "coerce_call_args",
        lambda _service, _method, args, kwargs: (args, kwargs),
    )
    monkeypatch.setattr(server_module, "_ensure_listwindow_frontend", lambda: {"ok": True})

    backend_port = 44001
    connect_calls = 0
    fail_next_call = False

    class ServiceLocation:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class BackendClient:
        def connect(self, _listener):
            nonlocal connect_calls
            connect_calls += 1

        def getDisplayName(self):
            nonlocal fail_next_call
            if fail_next_call:
                fail_next_call = False
                raise ConnectionResetError("backend disconnected")
            return "Example"

        def close(self):
            pass

    registry_module = SimpleNamespace(
        ServiceLocation=ServiceLocation,
        Protocol=SimpleNamespace(Binary=0),
        Transport=SimpleNamespace(Socket=0),
    )
    listwindow_module = SimpleNamespace(
        ListWindowBackend=object,
        ServiceRegistry=registry_module,
    )
    monkeypatch.setattr(
        server_module,
        "load_thrift_module",
        lambda *_args: listwindow_module,
    )
    monkeypatch.setattr(
        server_module,
        "_list_registry_services",
        lambda _name_filter: [
            {
                "name": "WIN_EXAMPLE",
                "host": "127.0.0.1",
                "port": backend_port,
                "transport": 0,
            }
        ],
    )
    monkeypatch.setattr(
        server_module,
        "make_client",
        lambda *_args, **_kwargs: BackendClient(),
    )

    assert server_module._call_listwindow("WIN_EXAMPLE", "getDisplayName") == "Example"
    assert connect_calls == 1
    assert server_module._call_listwindow("WIN_EXAMPLE", "getDisplayName") == "Example"
    assert connect_calls == 1

    backend_port = 44002
    assert server_module._call_listwindow("WIN_EXAMPLE", "getDisplayName") == "Example"
    assert connect_calls == 2

    fail_next_call = True
    with pytest.raises(ConnectionResetError, match="backend disconnected"):
        server_module._call_listwindow("WIN_EXAMPLE", "getDisplayName")
    assert "WIN_EXAMPLE" not in server_module._LISTWINDOW_CONNECTED

    assert server_module._call_listwindow("WIN_EXAMPLE", "getDisplayName") == "Example"
    assert connect_calls == 3


def test_listwindow_uses_snapshot_service_when_registered(server_module, monkeypatch):
    calls = []

    shown = []

    class SnapshotClient:
        def getOpenWindows(self):
            return ["Disassembly", "Watch"]

        def getAvailableWindows(self):
            return ["Disassembly", "Watch", "Locals"]

        def showWindow(self, window_id, show, focus):
            shown.append((window_id, show, focus))

        def getDisplayName(self, window_id):
            calls.append(("getDisplayName", window_id))
            return "Disassembly window"

        def getNumberOfRows(self, window_id):
            return 2

        def getRow(self, window_id, index):
            calls.append(("getRow", window_id, index))
            return {"row": index}

        def close(self):
            pass

    monkeypatch.setattr(
        server_module,
        "load_config",
        lambda: SimpleNamespace(timeout_ms=1000, include_dirs=[]),
    )
    monkeypatch.setattr(
        server_module,
        "_find_include_thrift",
        lambda _include_dirs, filename: Path(filename),
    )
    monkeypatch.setattr(
        server_module,
        "load_thrift_module",
        lambda *_args: SimpleNamespace(ListWindowSnapshot=object),
    )
    monkeypatch.setattr(
        server_module,
        "_list_registry_services",
        lambda _filter: [
            {"name": "listwindow.snapshot", "host": "127.0.0.1", "port": 45001, "transport": 0}
        ],
    )
    monkeypatch.setattr(server_module, "make_client", lambda *_a, **_k: SnapshotClient())
    monkeypatch.setattr(
        server_module,
        "_ensure_listwindow_frontend",
        lambda: pytest.fail("frontend callback server must not be needed for snapshots"),
    )

    # Case-insensitive id match; no ListWindowBackend.connect involved.
    assert server_module._call_listwindow("disassembly", "getDisplayName") == "Disassembly window"
    assert server_module._call_listwindow("disassembly", "isSliding") is False
    assert server_module._call_listwindow("disassembly", "setVisibleRows", 0, 1) is None
    rows = server_module.listwindow_get_rows("disassembly", 0, 5)
    assert rows["row_count"] == 2 and rows["returned"] == 2
    assert [name["name"] for name in server_module.listwindow_list_services("listwindow")] == [
        "Disassembly",
        "Locals",
        "Watch",
    ]
    assert server_module._call_listwindow("locals", "getDisplayName") == "Disassembly window"
    assert shown == [("Locals", True, False)]
    with pytest.raises(server_module.ThriftBridgeError, match="Open windows: Disassembly, Watch"):
        server_module._call_listwindow("nope", "getDisplayName")
    with pytest.raises(server_module.ThriftBridgeError, match="not supported"):
        server_module._call_listwindow("disassembly", "navigateToFraction", 0.5, 0, 1)
