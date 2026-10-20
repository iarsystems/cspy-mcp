from __future__ import annotations

import importlib
import sys
from collections import deque
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture()
def server_module():
    # Import lazily per-test so monkeypatching stays isolated.
    return importlib.import_module("mcp_thrift_server.server")


@pytest.fixture(autouse=True)
def allow_session_gated_tools_in_unit_tests(server_module):
    # Unit tests mock backend RPCs directly and are not expected to perform
    # full configure/start lifecycle unless explicitly testing that behavior.
    server_module._set_session_state(configured=True, started=True)
    yield


@pytest.fixture()
def reset_server_state(server_module):
    # Keep mutable globals clean between tests.
    server_module._LIBSUPPORT_OUTPUT_TEXT = deque(maxlen=5000)
    server_module._LIBSUPPORT_OUTPUT_BYTES = bytearray()
    server_module._LIBSUPPORT_INPUT_BYTES = bytearray()
    server_module._LIBSUPPORT_EXIT_CODE = None
    server_module._LIBSUPPORT_ASSERTS = deque(maxlen=100)

    server_module._LISTWINDOW_NOTES = deque(maxlen=500)
    server_module._LISTWINDOW_TOOLBAR_NOTES = deque(maxlen=500)
    server_module._LISTWINDOW_CONNECTED = {}
    server_module._set_session_state(configured=True, started=True)

    yield
