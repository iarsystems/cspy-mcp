"""coverage_* tools."""

from __future__ import annotations

import pytest

from iar_cspy import CSpyError, SessionError

# As the backend writes it (CssCodeCoverageHandler::getXMLData).
REPORT = (
    "<?xml version = \"1.0\"?><cpMetaDataInfo><program><name>app</name>"
    "<execCount>3</execCount><totalCount>5</totalCount></program>"
    "<module><name>main</name><execCount>3</execCount><totalCount>4</totalCount>"
    "<function><name>main</name><execCount>2</execCount><totalCount>3</totalCount>"
    "<sourceLoc file='/src/main.c' startLine = '10' endLine = '-1' startCol = '5' endCol = '-1'/>"
    "<step><name>init()</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/main.c' startLine = '12' endLine = '12' startCol = '3' endCol = '9'/></step>"
    "<step><name>work()</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/main.c' startLine = '13' endLine = '13' startCol = '3' endCol = '9'/></step>"
    "<step><name>return 1</name><execCount>0</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/main.c' startLine = '14' endLine = '14' startCol = '3' endCol = '11'/></step>"
    "</function>"
    "<function><name>init</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/init.c' startLine = '1' endLine = '-1' startCol = '1' endCol = '-1'/>"
    "<step><name>x = 0</name><execCount>1</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/init.c' startLine = '2' endLine = '2' startCol = '3' endCol = '8'/></step>"
    "</function></module>"
    "<module><name>lib</name><execCount>0</execCount><totalCount>1</totalCount>"
    "<function><name>unused</name><execCount>0</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/lib.c' startLine = '7' endLine = '-1' startCol = '1' endCol = '-1'/>"
    "<step><name>return</name><execCount>0</execCount><totalCount>1</totalCount>"
    "<sourceLoc file='/src/lib.c' startLine = '8' endLine = '8' startCol = '3' endCol = '9'/></step>"
    "</function></module></cpMetaDataInfo>"
)


@pytest.fixture()
def report(fake):
    client, rpc = fake
    rpc.on("codecoverage", "isInitialized", True)
    rpc.on("codecoverage", "refreshMetaData", lambda: None)
    rpc.on("codecoverage", "getXMLData", REPORT)
    return rpc


def test_status_of_a_supporting_driver(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "hasMetaDataSupport", True)
    rpc.on("codecoverage", "isEnabled", True)
    rpc.on("codecoverage", "isInitialized", False)
    rpc.on("codecoverage", "getSessionId", 42)
    out = server.coverage_get_status()
    assert out["ok"] is True
    assert out["data"] == {"supported": True, "enabled": True, "initialized": False, "session_id": 42}


def test_status_without_support_says_only_that(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "hasMetaDataSupport", False)
    out = server.coverage_get_status()
    assert out == {"ok": True, "tool": "coverage_get_status", "data": {"supported": False}, "error": None}
    assert rpc.methods("codecoverage") == ["hasMetaDataSupport"]


def test_status_of_an_unregistered_service_is_an_envelope(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "hasMetaDataSupport", CSpyError("Failed to resolve service 'codecoverage' via registry"))
    out = server.coverage_get_status()
    assert out["ok"] is False
    assert "supports code coverage" in out["error"]["message"]


def test_set_enabled_on_and_off(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "enable", True)
    assert server.coverage_set_enabled()["data"] == {"enabled": True}
    assert server.coverage_set_enabled(False)["data"] == {"enabled": False}
    assert rpc.args("codecoverage", "enable") == [(True,), (False,)]


def test_set_enabled_failure_is_an_envelope(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "enable", False)
    rpc.on("codecoverage", "isEnabled", False)
    out = server.coverage_set_enabled(True)
    assert out["ok"] is False and "failed to enable" in out["error"]["message"]


def test_reset(server, fake):
    _, rpc = fake
    rpc.on("codecoverage", "reinitMetaData", True)
    assert server.coverage_reset()["ok"] is True
    rpc.on("codecoverage", "reinitMetaData", False)
    assert server.coverage_reset()["ok"] is False


def test_report_per_function_by_default(server, report):
    out = server.coverage_get_report()
    assert out["ok"] is True
    data = out["data"]
    assert data["program"] == {"name": "app", "exec_count": 3, "total_count": 5, "percent": 60.0}
    assert data["count"] == 3 and data["truncated"] is False
    assert data["items"][0] == {
        "module": "main",
        "function": "main",
        "file": "/src/main.c",
        "line": 10,
        "exec_count": 2,
        "total_count": 3,
        "percent": 66.7,
    }
    assert report.methods("codecoverage") == ["isInitialized", "refreshMetaData", "getXMLData"]


def test_report_per_module(server, report):
    items = server.coverage_get_report(level="module")["data"]["items"]
    assert items == [
        {"module": "main", "exec_count": 3, "total_count": 4, "percent": 75.0},
        {"module": "lib", "exec_count": 0, "total_count": 1, "percent": 0.0},
    ]


def test_uncovered_steps_in_a_file(server, report):
    data = server.coverage_get_report(level="step", match="MAIN.C", uncovered_only=True)["data"]
    assert data["items"] == [
        {
            "module": "main",
            "function": "main",
            "code": "return 1",
            "file": "/src/main.c",
            "start_line": 14,
            "start_col": 3,
            "end_line": 14,
            "end_col": 11,
            "status": "not_executed",
            "exec_count": 0,
            "total_count": 1,
        }
    ]


def test_match_covers_module_function_and_file(server, report):
    names = lambda **kw: [i["function"] for i in server.coverage_get_report(**kw)["data"]["items"]]  # noqa: E731
    assert names(match="lib") == ["unused"]
    assert names(match="ini") == ["init"]
    assert names(uncovered_only=True) == ["main", "unused"]


def test_report_truncates(server, report):
    data = server.coverage_get_report(level="step", max_items=2)["data"]
    assert data["count"] == 5 and data["truncated"] is True and len(data["items"]) == 2


def test_report_without_refresh(server, report):
    server.coverage_get_report(refresh=False)
    assert report.methods("codecoverage") == ["isInitialized", "getXMLData"]


def test_report_rejects_an_unknown_level(server, report):
    out = server.coverage_get_report(level="line")
    assert out["ok"] is False and "level must be one of" in out["error"]["message"]
    assert report.calls == []


def test_xml_is_raw_and_truncates(server, report):
    data = server.coverage_get_xml(max_chars=20)["data"]
    assert data == {"length": len(REPORT), "truncated": True, "xml": REPORT[:20]}
    assert server.coverage_get_xml()["data"]["xml"] == REPORT


@pytest.mark.parametrize(
    "tool", ["coverage_get_status", "coverage_set_enabled", "coverage_reset", "coverage_get_report", "coverage_get_xml"]
)
def test_tools_need_a_session(server, fake, tool):
    client, _ = fake
    client.debugger.set_state(configured=False, started=False)
    with pytest.raises(SessionError, match=tool):
        getattr(server, tool)()


def test_step_status():
    from iar_cspy_mcp.tools.coverage import _step_status

    assert [_step_status(e, t) for e, t in [(0, 2), (6, 10), (10, 10)]] == ["not_executed", "partial", "executed"]
