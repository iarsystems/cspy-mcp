"""crun_* tools."""

from __future__ import annotations

from types import SimpleNamespace

from iar_cspy import CSpyError
from iar_cspy.testing import crun_message


def _rules_window(rows, columns=("Check", "Source File", "Action")):
    return SimpleNamespace(
        overview=lambda: SimpleNamespace(columns=[{"title": t} for t in columns]),
        rows=lambda: SimpleNamespace(
            rows=[{"index": i, "row": {"cells": [{"text": t} for t in row]}} for i, row in enumerate(rows)]
        ),
    )


def test_crun_list_rules_reads_the_rules_window(server, fake):
    client, _ = fake
    asked = []

    def window(name):
        asked.append(name)
        return _rules_window([("AssertFailure", "main.c", "Log"), ("*", "*", "Stop")])

    client.listwindows.window = window

    out = server.crun_list_rules()
    assert out["ok"] is True
    assert asked == ["WIN_C_RUN_MESSAGE_RULES"]
    assert out["data"]["count"] == 2
    assert out["data"]["rules"][0] == {"index": 0, "cells": {"Check": "AssertFailure", "Source File": "main.c", "Action": "Log"}}
    assert out["data"]["rules"][1]["cells"]["Action"] == "Stop"


def test_crun_list_rules_failure_is_an_envelope(server, fake):
    client, _ = fake

    def window(name):
        raise CSpyError("List window 'WIN_C_RUN_MESSAGE_RULES' is not open in the IDE.")

    client.listwindows.window = window

    out = server.crun_list_rules()
    assert out["ok"] is False
    assert "not open" in out["error"]["message"]


def test_crun_add_rule_picks_the_call_from_the_location(server, fake):
    _, rpc = fake
    rpc.on("crun", "addRuleOnMessage", None)
    rpc.on("crun", "addRuleOnMessageFile", None)
    rpc.on("crun", "addRuleOnMessageRange", None)

    assert server.crun_add_rule("AssertFailure")["ok"] is True
    assert rpc.args("crun", "addRuleOnMessage") == [("AssertFailure",)]

    assert server.crun_add_rule("AssertFailure", file="main.c")["ok"] is True
    assert rpc.args("crun", "addRuleOnMessageFile") == [("AssertFailure", "main.c")]

    out = server.crun_add_rule("AssertFailure", file="main.c", start_line=10, start_col=1)
    assert out["ok"] is True
    [(name, range_)] = rpc.args("crun", "addRuleOnMessageRange")
    assert range_["first"] == {"filename": "main.c", "line": 10, "col": 1}
    assert range_["last"] == {"filename": "main.c", "line": 10, "col": 1}


def test_crun_add_rule_range_without_a_file_is_an_envelope(server, fake):
    out = server.crun_add_rule("AssertFailure", start_line=10)
    assert out["ok"] is False
    assert "needs 'file'" in out["error"]["message"]


def test_crun_default_action_get_and_set(server, fake):
    _, rpc = fake
    rpc.on("crun", "setDefaultAction", None)
    rpc.on("crun", "getDefaultAction", 1)

    assert server.crun_set_default_action("LOG")["ok"] is True
    assert rpc.args("crun", "setDefaultAction") == [(1,)]

    out = server.crun_get_default_action()
    assert out["ok"] is True
    assert out["data"]["action"] == "LOG"


def test_crun_not_initialized_is_a_structured_error(server, fake):
    _, rpc = fake

    def not_initialized(*args, **kwargs):
        raise Exception("C-RUN is not initialized")

    rpc.on("crun", "getDefaultAction", not_initialized)

    out = server.crun_get_default_action()
    assert out["ok"] is False
    assert out["error"]["code"] == "CRUN_NOT_INITIALIZED"


def _display(client):
    """Push as the backend would: through the CRunDisplay handler iar_cspy hosts."""
    return client.crun_messages.server.handler


def test_crun_get_messages_reports_what_the_backend_pushed(server, fake):
    client, _ = fake
    handler = _display(client)
    handler.itemAdded(crun_message(index=0, name="Division by zero", text="x / 0"), "core0")
    handler.itemAdded(crun_message(index=1, name="Access out of bounds"), "core0")
    handler.itemAdded(crun_message(index=0, name="Other"), "core1")

    out = server.crun_get_messages()
    assert out["ok"] is True
    assert out["data"]["namespaces"] == ["core0", "core1"]
    assert out["data"]["count"] == 3
    assert [m["name"] for m in out["data"]["messages"]] == ["Division by zero", "Access out of bounds", "Other"]
    assert out["data"]["messages"][0]["namespace"] == "core0"
    assert out["data"]["messages"][0]["break_action"] == "LOG"

    only = server.crun_get_messages(namespace="core1")
    assert [m["name"] for m in only["data"]["messages"]] == ["Other"]


def test_crun_get_messages_truncates(server, fake):
    client, _ = fake
    for i in range(5):
        _display(client).itemAdded(crun_message(index=i), "")

    out = server.crun_get_messages(max_messages=2)
    assert (out["data"]["count"], out["data"]["truncated"], len(out["data"]["messages"])) == (5, True, 2)


def test_crun_clear_messages(server, fake):
    client, _ = fake
    _display(client).itemAdded(crun_message(index=0), "core0")
    _display(client).itemAdded(crun_message(index=0), "core1")

    assert server.crun_clear_messages("core0")["ok"] is True
    assert server.crun_get_messages()["data"]["namespaces"] == ["core1"]
    server.crun_clear_messages()
    assert server.crun_get_messages()["data"]["count"] == 0
