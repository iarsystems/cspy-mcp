"""logs_* tools and the build problems from the IDE's Build log."""

from __future__ import annotations

import dataclasses
import threading
from types import SimpleNamespace

import pytest

from iar_cspy import Config
from iar_cspy.testing import fake_client, log_entry
from iar_cspy_mcp import server as server_module
from iar_cspy_mcp.tools import logs as logs_tools

ERROR = 4
WARNING = 3
ALERT = 5
SUPER = 6


def post(client, **fields) -> None:
    client.logs.server.handler.postLogEntry(log_entry(**fields))


@pytest.fixture()
def listening(fake):
    client, rpc = fake
    assert client.logs.ensure_registered()["ok"] is True
    return client, rpc


def test_status_reports_listening_and_per_log_counts(server, listening):
    client, _ = listening
    client.logs.server.handler.addCategory("Log_Build")
    client.logs.server.handler.startSession("Log_Build")
    post(client, category="Log_Build", text="Error[Pe020]: x undefined", severity=ERROR)
    post(client, category="Log_Build", text="Warning[Pe550]: y set but never used", severity=WARNING)

    out = server.logs_status()
    assert out["ok"] is True
    data = out["data"]
    assert data["listening"] is True and data["listening_since"]
    [build] = data["categories"]
    assert build["name"] == "Log_Build" and build["open"] is True
    assert (build["errors"], build["warnings"], build["entries"]) == (1, 1, 2)
    assert data["mark"] == build["last_seq"] == 2


@pytest.fixture()
def not_listening():
    client, rpc = fake_client(dataclasses.replace(fake_client()[0].config, auto_log_service=False))
    server_module.set_client(client)
    yield client, rpc
    server_module.set_client(None)
    client.close()


def test_read_without_a_listener_is_an_unavailable_envelope(server, not_listening):
    out = server.logs_read("Log_Build")
    assert out["ok"] is False
    assert out["error"]["code"] == "LOGS_UNAVAILABLE"
    assert "THRIFT_AUTO_LOG_SERVICE" in out["error"]["message"]


def test_read_pages_by_seq_both_ways(server, listening):
    client, _ = listening
    for i in range(10):
        post(client, category="Log_Debug", text=f"line {i}")

    newest = server.logs_read("Log_Debug", limit=3)["data"]
    assert newest["text"] == "line 7\nline 8\nline 9"
    assert newest["page"] == {
        "returned": 3, "total": 10, "before": 7, "after": 0,
        "first_seq": 8, "last_seq": 10, "truncated": False,
    }

    older = server.logs_read("Log_Debug", before=newest["page"]["first_seq"], limit=3)["data"]
    assert older["text"] == "line 4\nline 5\nline 6"
    assert older["page"]["before"] == 4 and older["page"]["after"] == 3

    # Following: nothing new, then exactly what came after.
    assert server.logs_read(after=newest["page"]["last_seq"])["data"]["page"]["returned"] == 0
    post(client, category="Log_Debug", text="line 10")
    again = server.logs_read(after=newest["page"]["last_seq"])["data"]
    assert again["text"] == "[Log_Debug] line 10"


def test_read_severity_filter_keeps_detail_lines_and_marks_them(server, listening):
    client, _ = listening
    post(client, text="Building main.c")
    post(client, text="Error[Pe020]: identifier \"x\" is undefined", severity=ERROR,
         path="/w/main.c", srcPos=(12, 5))
    post(client, text="detected during instantiation", isSubEntry=True)
    post(client, text="Done")

    out = server.logs_read("Log_Build", min_severity="warning")["data"]
    assert out["text"].splitlines() == [
        '/w/main.c:12:5: Error[Pe020]: identifier "x" is undefined',
        "  detected during instantiation",
    ]

    entries = server.logs_read("Log_Build", min_severity="ERROR", format="entries")["data"]["entries"]
    assert entries[0]["path"] == "/w/main.c" and entries[0]["line"] == 12
    assert entries[0]["severity"] == "ERROR" and entries[1]["is_sub_entry"] is True


def test_text_lines_do_not_repeat_what_the_text_already_says(server, listening):
    client, _ = listening
    post(client, text="main.c(12): undefined symbol", severity=WARNING, path="/w/main.c", srcPos=(12, 1))
    post(client, text="Linker failed", severity=ERROR)
    post(client, text="/w/x.c(3) : bad", severity=ERROR, path="/w/x.c", srcPos=(3, 1))
    assert server.logs_read("Log_Build")["data"]["text"].splitlines() == [
        "Warning: main.c(12): undefined symbol",
        "Error: Linker failed",
        "Error: /w/x.c(3) : bad",
    ]


def test_read_match_and_sessions(server, listening):
    client, _ = listening
    handler = client.logs.server.handler
    handler.startSession("Log_Build")
    post(client, text="old build: Error x", severity=ERROR)
    handler.startSession("Log_Build")
    post(client, text="new build: ok")
    post(client, text="new build: Error y", severity=ERROR)

    assert server.logs_read("Log_Build")["data"]["text"] == "new build: ok\nError: new build: Error y"
    assert server.logs_read("Log_Build", session="all", match="error")["data"]["page"]["total"] == 2
    assert server.logs_read("Log_Build", session="1")["data"]["text"] == "Error: old build: Error x"
    regex = server.logs_read("Log_Build", session="all", match=r"error [xy]$", regex=True)
    assert regex["data"]["page"]["total"] == 2


def test_read_bad_arguments_are_envelopes(server, listening):
    for kwargs, needle in [
        ({"min_severity": "loud"}, "Unknown log severity"),
        ({"session": "latest"}, "session must be"),
        ({"match": "(", "regex": True}, "Invalid regex"),
        ({"after": "x"}, "seq number"),
        ({"format": "xml"}, "format must be"),
    ]:
        out = server.logs_read(**kwargs)
        assert out["ok"] is False, kwargs
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert needle in out["error"]["message"]


def test_a_page_of_long_lines_ends_early_and_the_next_one_continues(server, listening, monkeypatch):
    client, _ = listening
    monkeypatch.setattr(logs_tools, "MAX_PAGE_CHARS", 250)
    for i in range(5):
        post(client, category="Log_Tool_Output", text=f"{i}" * 100)

    first = server.logs_read("Log_Tool_Output", after=0)["data"]
    assert first["page"]["truncated"] is True
    assert first["page"]["returned"] == 2 and first["page"]["after"] == 3
    rest = server.logs_read("Log_Tool_Output", after=first["page"]["last_seq"])["data"]
    assert rest["text"].splitlines()[0] == "2" * 100


def test_wait_for_finds_a_later_entry_and_times_out_quietly(server, listening):
    client, _ = listening
    post(client, category="Log_Debug", text="Download complete")

    # Only entries from now on, unless `after` says otherwise.
    assert server.logs_wait_for("download", timeout_ms=50)["data"]["found"] is False
    found = server.logs_wait_for("download", after=0, timeout_ms=50)["data"]
    assert found["found"] is True and found["cursor"] == 1
    assert found["line"] == "[Log_Debug] Download complete"

    timer = threading.Timer(0.05, post, args=(client,), kwargs={"category": "Log_Debug", "text": "Hello from target"})
    timer.start()
    try:
        out = server.logs_wait_for("hello", category="Log_Debug", timeout_ms=5000)
    finally:
        timer.join()
    assert out["ok"] is True and out["data"]["found"] is True
    assert out["data"]["entry"]["text"] == "Hello from target"


def test_build_reports_problems_from_the_build_log(server, listening):
    client, rpc = listening
    post(client, text="Error from an earlier build", severity=ERROR)

    def build(*_args):
        post(client, text="Building main.c")
        post(client, text="Error[Pe020]: x undefined", severity=ERROR, path="/w/main.c", srcPos=(3, 1))
        post(client, text="  in function main", isSubEntry=True)
        post(client, text="Warning[Pe177]: y unused", severity=WARNING, path="/w/util.c", srcPos=(9, 2))
        return {"succeded": False, "buildOutput": ["error"]}

    rpc.on("projectmanager", "BuildProject", build)
    out = server.project_build("/tmp/p.ewp", "Debug")
    assert out["ok"] is False
    problems = out["data"]["problems"]
    assert (problems["errors"], problems["warnings"]) == (1, 1)
    assert problems["lines"] == [
        "/w/main.c:3:1: Error[Pe020]: x undefined",
        "    in function main",
        "/w/util.c:9:2: Warning[Pe177]: y unused",
    ]
    assert problems["after"] == 1 and problems["truncated"] is False


def test_build_without_the_logs_has_null_problems(server, not_listening):
    _, rpc = not_listening
    rpc.on("projectmanager", "BuildProject", {"succeded": True, "buildOutput": ["ok"]})
    out = server.project_build("/tmp/p.ewp", "Debug")
    assert out["ok"] is True and out["data"]["problems"] is None


def test_status_explains_a_backend_without_a_forwarder(server):
    client, _ = fake_client(Config(mode="managed"))
    server_module.set_client(client)
    try:
        client.logs.ensure_registered = lambda: {"ok": True}
        client.logs._registered_at = ("127.0.0.1", 1)
        data = server.logs_status()["data"]
    finally:
        server_module.set_client(None)
        client.close()
    assert data["listening"] is True
    assert "CSpy_Debug" in data.get("note", "")


def test_highlighted_lines_are_not_problems(server, listening):
    """A broken build as a live iaride logged it: ALERT/SUPER are headlines, not errors."""
    client, rpc = listening

    def build(*_args):
        post(client, text="", severity=ALERT)
        post(client, text="arm_main - Debug", severity=ALERT)
        post(client, text="main.c", severity=SUPER)
        post(client, text='Error[Pe020]: identifier "u" is undefined', severity=ERROR, path="/w/main.c", srcPos=(198, 43))
        post(client, text='Warning[Pe177]: variable "x" was declared but never referenced', severity=WARNING,
             path="/w/main.c", srcPos=(198, 28))
        post(client, text="", severity=SUPER)
        post(client, text="Total number of errors: 1", severity=SUPER, isSubEntry=True)
        post(client, text="Resolving dependencies...", severity=ALERT)
        post(client, text="Build failed", severity=SUPER)
        return {"succeded": False, "buildOutput": []}

    rpc.on("projectmanager", "BuildProject", build)
    problems = server.project_build("/tmp/p.ewp", "Debug")["data"]["problems"]
    assert (problems["errors"], problems["warnings"]) == (1, 1)
    assert problems["lines"] == [
        '/w/main.c:198:43: Error[Pe020]: identifier "u" is undefined',
        '/w/main.c:198:28: Warning[Pe177]: variable "x" was declared but never referenced',
    ]

    read = server.logs_read("Log_Build", problems=True)["data"]
    assert read["page"]["total"] == 2
    # Prominence, not problems: "Build failed" is SUPER and has no Error: prefix.
    prominent = server.logs_read("Log_Build", min_severity="ALERT")["data"]["text"].splitlines()
    assert "Build failed" in prominent and "Resolving dependencies..." in prominent
    [build_log] = server.logs_status()["data"]["categories"]
    assert (build_log["errors"], build_log["warnings"]) == (1, 1)


def debugger_log(client, cat, text) -> None:
    client.events.server.handler.postLogEvent(SimpleNamespace(cat=cat, text=text, timestamp=0))


def test_debugger_get_log_keeps_its_shape(server, fake):
    client, _ = fake
    debugger_log(client, 1, "Download complete.\n")
    debugger_log(client, 2, "Breakpoint moved")
    debugger_log(client, 3, "Flash failed")
    assert server.debugger_get_log() == ["Download complete.", "Warning: Breakpoint moved", "Error: Flash failed"]
    assert server.debugger_get_log(max_lines=1) == ["Error: Flash failed"]
    assert server.debugger_get_log(max_lines=0) == []


def test_the_debuggers_log_is_readable_without_a_forwarder(server, not_listening):
    client, _ = not_listening
    debugger_log(client, 3, "Flash failed")

    out = server.logs_read("CSpy_Debug", problems=True)
    assert out["ok"] is True and out["data"]["text"] == "Error: Flash failed"
    everything = server.logs_read()["data"]
    assert everything["text"] == "[CSpy_Debug] Error: Flash failed"
    assert "THRIFT_AUTO_LOG_SERVICE" in everything["note"]
    found = server.logs_wait_for("flash", category="CSpy_Debug", after=0, timeout_ms=10)["data"]
    assert found["found"] is True


def test_status_names_the_category_with_the_debug_log(server, listening):
    client, _ = listening
    assert server.logs_status()["data"]["debug_log"] is None
    debugger_log(client, 1, "Download complete.")
    assert server.logs_status()["data"]["debug_log"] == "CSpy_Debug"
    post(client, category="Log_Debug", text="Download complete.")
    assert server.logs_status()["data"]["debug_log"] == "Log_Debug"
