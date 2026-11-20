"""project_option_categories / project_get_options / project_set_options.

These tools exist because a GUI iaride publishes ProjectManager but no
OptionsService. What is worth pinning here is the value validation: the backend
accepts a bad boolean silently and resolves it to "off", so the tools must
refuse one before anything is written.
"""

from __future__ import annotations

import json

import pytest

from iar_cspy.testing import project_manager_error
from iar_cspy_mcp.tools import project_options


@pytest.fixture(autouse=True)
def _clear_title_cache():
    project_options._TITLES.clear()
    yield
    project_options._TITLES.clear()


@pytest.fixture()
def pm(fake):
    client, rpc = fake
    rpc.on("projectmanager", "GetCurrentProject", {"filename": "/w/p.ewp"})
    rpc.on("projectmanager", "GetCurrentConfiguration", {"name": "Debug"})
    return client, rpc, client.idl.module("projectmanager.thrift")


def element(mod, eid, label="", enabled=True):
    return mod.OptionElementDescription(id=eid, label=label, enabled=enabled, data="", description="")


def option(mod, oid, value, type=0, elements=(), enabled=True, visible=True, description=""):
    return mod.OptionDescription(
        id=oid,
        value=value,
        type=type,
        elements=[element(mod, *e) if isinstance(e, tuple) else element(mod, e) for e in elements],
        enabled=enabled,
        visible=visible,
        canBeLocal=False,
        description=description,
    )


def test_option_categories_reports_counts(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionCategories", [
        mod.OptionCategory(id="RuntimeChecking", optionIds=["a", "b"]),
        mod.OptionCategory(id="ICCARM", optionIds=["c"]),
    ])
    out = server.project_option_categories()
    assert out["ok"] is True
    assert out["data"]["categories"] == [
        {"id": "RuntimeChecking", "option_count": 2},
        {"id": "ICCARM", "option_count": 1},
    ]


def test_get_options_by_category_reads_only_that_category(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionCategories", [
        mod.OptionCategory(id="RuntimeChecking", optionIds=["RuntimeChecking.@GenRtcEnable"]),
    ])
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])
    rpc.on("projectmanager", "GetToolchains", [mod.Toolchain(id="ARM")])
    rpc.on("projectmanager", "GetPresentationForOptionsAsJson",
           json.dumps({"settings": [{"id": "RuntimeChecking.@GenRtcEnable", "title": "&Enable"}]}))

    out = server.project_get_options(category="runtimechecking")
    assert out["ok"] is True
    assert out["data"]["category"] == "RuntimeChecking"
    (opt,) = out["data"]["options"]
    assert opt["id"] == "RuntimeChecking.@GenRtcEnable"
    assert opt["value"] == "Off"
    assert opt["type"] == "Check"
    assert opt["title"] == "Enable"  # the & accelerator is stripped
    # only the category's ids were asked for
    assert rpc.args("projectmanager", "GetOptionsForConfiguration")[0][2] == [
        "RuntimeChecking.@GenRtcEnable"
    ]


def test_get_options_search_and_truncation(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "RuntimeChecking.@GenRtcDivByZero", "Off"),
        option(mod, "RuntimeChecking.@GenRtcIntOverflow", "Off"),
        option(mod, "ICCARM.@CCOptLevel", "1", type=4, elements=["0", "1"]),
    ])
    out = server.project_get_options(search="genrtc", max_results=1)
    assert out["data"]["total_matched"] == 2
    assert out["data"]["truncated"] is True
    assert len(out["data"]["options"]) == 1


@pytest.mark.parametrize("needle", ["runtime checking", "RuntimeChecking", "division by zero", "div zero"])
def test_get_options_search_ignores_spacing_and_case(server, pm, needle):
    """Ids are unspaced camel-case; people type words."""
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "RuntimeChecking.@GenRtcDivByZero", "Off", description="Division by zero"),
        option(mod, "ICCARM.@CCOptLevel", "1", type=4, elements=["0", "1"]),
    ])
    out = server.project_get_options(search=needle)
    assert [o["id"] for o in out["data"]["options"]] == ["RuntimeChecking.@GenRtcDivByZero"]


def test_get_options_unknown_category_lists_the_real_ones(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionCategories", [mod.OptionCategory(id="ICCARM", optionIds=[])])
    out = server.project_get_options(category="nope")
    assert out["ok"] is False
    assert out["error"]["code"] == "NO_SUCH_CATEGORY"
    assert out["data"]["categories"] == ["ICCARM"]


def test_set_options_applies_and_reports_before_and_after(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])
    rpc.on("projectmanager", "ApplyOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "On")])

    out = server.project_set_options('{"RuntimeChecking.@GenRtcEnable": "On"}')
    assert out["ok"] is True
    assert out["data"]["results"] == [{
        "id": "RuntimeChecking.@GenRtcEnable",
        "before": "Off", "requested": "On", "actual": "On",
        "changed": True, "coerced": False,
    }]
    assert out["data"]["persisted_to_disk"] is False


def test_set_options_rejects_a_bad_boolean_without_writing(server, pm):
    """The backend would accept "Yes" and silently resolve it to off."""
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])

    out = server.project_set_options('{"RuntimeChecking.@GenRtcEnable": "Yes"}')
    assert out["ok"] is False
    assert out["error"]["code"] == "INVALID_OPTION_VALUE"
    assert "turn the option OFF" in out["error"]["message"]
    assert "ApplyOptionsForConfiguration" not in rpc.methods("projectmanager")


def test_set_options_keeps_each_checks_own_boolean_spelling(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "C-STAT.AnalyzeTimeoutEnabled", "false"),
        option(mod, "General.@OGBufferedTerminalOutput", "Off"),
    ])
    rpc.on("projectmanager", "ApplyOptionsForConfiguration", [])
    server.project_set_options(
        '{"C-STAT.AnalyzeTimeoutEnabled": "On", "General.@OGBufferedTerminalOutput": "true"}'
    )
    sent = rpc.args("projectmanager", "ApplyOptionsForConfiguration")[0][2]
    assert {o["id"]: o["value"] for o in sent} == {
        "C-STAT.AnalyzeTimeoutEnabled": "true",
        "General.@OGBufferedTerminalOutput": "On",
    }


def test_set_options_rejects_a_value_that_is_not_an_element_id(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "General.@GRuntimeLibSelect", "DLibFull", type=3,
               elements=["None", "DLibNormal", "DLibFull"]),
    ])
    out = server.project_set_options('{"General.@GRuntimeLibSelect": "2"}')
    assert out["ok"] is False
    assert "not a legal value" in out["error"]["message"]
    assert "'DLibNormal'" in out["error"]["message"]
    assert "ApplyOptionsForConfiguration" not in rpc.methods("projectmanager")


def test_set_options_rejects_a_mis_sized_checklist_mask(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "ICCARM.@CCAllowList", "000", type=5, elements=["0", "1", "2"]),
    ])
    out = server.project_set_options('{"ICCARM.@CCAllowList": "1"}')
    assert out["ok"] is False
    assert "3 characters" in out["error"]["message"]


def test_set_options_surfaces_backend_coercion(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [
        option(mod, "General.@OGCoreOrChip", "Core", type=4, elements=["Core", "Chip"]),
    ])
    rpc.on("projectmanager", "ApplyOptionsForConfiguration",
           [option(mod, "General.@OGCoreOrChip", "Chip", type=4)])
    out = server.project_set_options('{"General.@OGCoreOrChip": "Chip"}')
    assert out["data"]["coerced"] == []
    assert out["data"]["results"][0]["actual"] == "Chip"


def test_set_options_unknown_id_is_refused_with_a_discovery_hint(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration", [])
    out = server.project_set_options('{"Nope.@Nope": "On"}')
    assert out["ok"] is False
    assert "Unknown option id(s): Nope.@Nope" in out["error"]["message"]
    assert "project_get_options" in out["error"]["message"]


def test_verify_only_does_not_apply(server, pm):
    _, rpc, mod = pm
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])
    rpc.on("projectmanager", "VerifyOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "On")])
    out = server.project_set_options('{"RuntimeChecking.@GenRtcEnable": "On"}', verify_only=True)
    assert out["ok"] is True
    assert out["data"]["verify_only"] is True
    assert "ApplyOptionsForConfiguration" not in rpc.methods("projectmanager")


def test_set_options_retries_a_busy_refusal(server, pm, monkeypatch):
    _, rpc, mod = pm
    monkeypatch.setattr(project_options, "BUSY_BACKOFF_S", 0)
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])
    rpc.sequence(
        "projectmanager", "ApplyOptionsForConfiguration",
        project_manager_error(description="a build is in progress"),
        [option(mod, "RuntimeChecking.@GenRtcEnable", "On")],
    )
    out = server.project_set_options('{"RuntimeChecking.@GenRtcEnable": "On"}')
    assert out["ok"] is True
    assert len(rpc.args("projectmanager", "ApplyOptionsForConfiguration")) == 2


def test_set_options_busy_throughout_is_a_retryable_envelope(server, pm, monkeypatch):
    _, rpc, mod = pm
    monkeypatch.setattr(project_options, "BUSY_BACKOFF_S", 0)
    rpc.on("projectmanager", "GetOptionsForConfiguration",
           [option(mod, "RuntimeChecking.@GenRtcEnable", "Off")])
    rpc.on("projectmanager", "ApplyOptionsForConfiguration",
           project_manager_error(description="a build is in progress"))
    out = server.project_set_options('{"RuntimeChecking.@GenRtcEnable": "On"}')
    assert out["ok"] is False
    assert out["error"]["code"] == "PROJECTMANAGER_BUSY"
    assert out["error"]["retryable"] is True
