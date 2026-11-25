"""Project option tools over ProjectManager (no OptionsService needed).

ProjectManager serves a flat, id-keyed list of every build and debug option, and
is the only one of the two IDE services a GUI `iaride` publishes. These tools add
what a caller needs on top of the raw RPCs: filtered discovery over ~2000
options, human titles from the presentation document, and validation of a value
against the option's own `elements` before it is written.

That validation is the point. The backend accepts an unrecognised boolean and
silently resolves it to "off", so a typo disables the check you meant to enable.
See docs/project-manager-for-settings.md.
"""

from __future__ import annotations

import re
import time
from typing import Any

from iar_cspy import BusyError, CSpyError
from iar_cspy.errors import error_entry

from .._app import envelope, get_client, mcp
from ._args import parse_json
from .project import _busy, resolve_project

OPTION_TYPES = {
    0: "Check", 1: "Edit", 2: "EditB", 3: "List", 4: "Radio", 5: "CheckList",
    6: "BuildActions", 7: "DeviceSelection", 8: "CMSISDevice", 9: "CMakeSettings",
    10: "PathList", 11: "EditHexInteger", 12: "JsonKeyValueMap", 13: "CheckTree",
}

CHOICE_TYPES = {3, 4, 7, 8}
BUSY_RETRIES = 5
BUSY_BACKOFF_S = 1.5

_TITLES: dict[str, dict[str, str]] = {}


def _strip_accelerator(title: str) -> str:
    return re.sub(r"&(?=\w)", "", title)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _matches(needle: str, *haystacks: str) -> bool:
    """Every word of ``needle`` somewhere in the text, ignoring case and punctuation.

    Option ids are camel-case and unspaced (``RuntimeChecking.@GenRtcEnable``)
    while people type "runtime checking", so a plain substring test over the
    raw strings misses the search that matters most.
    """
    blob = " ".join(_squash(h) for h in haystacks if h)
    return all(_squash(word) in blob for word in needle.split() if _squash(word))


def _titles() -> dict[str, str]:
    """Option id -> GUI title, from the presentation document.

    Absent from build-tools-only installations, and upstream calls it a
    GUI-building interface rather than a contract, so every failure here is
    non-fatal: callers just lose the human titles.
    """
    client = get_client()
    try:
        toolchains = client.project.call("GetToolchains")
        toolchain = str(getattr(toolchains[0], "id", "") or "")
    except Exception:  # noqa: BLE001
        return {}
    if not toolchain:
        return {}
    if toolchain in _TITLES:
        return _TITLES[toolchain]

    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            oid, title = node.get("id"), node.get("title")
            if isinstance(oid, str) and isinstance(title, str):
                found.setdefault(oid, _strip_accelerator(title))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    try:
        walk(parse_json(client.project.call("GetPresentationForOptionsAsJson", toolchain)))
    except Exception:  # noqa: BLE001
        found = {}
    _TITLES[toolchain] = found
    return found


def _element_data(element: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(getattr(element, "id", "")),
        "label": str(getattr(element, "label", "") or ""),
        "enabled": bool(getattr(element, "enabled", True)),
    }
    children = getattr(element, "elements", None)
    if children:
        data["elements"] = [_element_data(child) for child in children]
    return data


def _option_data(option: Any, titles: dict[str, str], include_elements: bool) -> dict[str, Any]:
    option_type = int(getattr(option, "type", -1))
    data: dict[str, Any] = {
        "id": option.id,
        "title": titles.get(option.id),
        "value": option.value,
        "type": OPTION_TYPES.get(option_type, option_type),
        "enabled": bool(option.enabled),
        "visible": bool(option.visible),
        "can_be_local": bool(getattr(option, "canBeLocal", False)),
    }
    description = getattr(option, "description", "") or ""
    if description:
        data["description"] = description
    if include_elements and getattr(option, "elements", None):
        data["elements"] = [_element_data(e) for e in option.elements]
    return data


def _read(ctx: dict[str, Any], cfg: str, ids: list[str]) -> list[Any]:
    return get_client().project.call("GetOptionsForConfiguration", ctx, cfg, ids)


def _categories(ctx: dict[str, Any], cfg: str) -> list[Any]:
    return get_client().project.call("GetOptionCategories", ctx, cfg)


# Only spellings the backend itself resolves the same way. It reads anything
# else - "Yes", "" - as "off" without complaining, so accepting a wider
# vocabulary here would silently disagree with the project it is writing to.
_BOOL_WORDS = {
    "on": True, "true": True, "1": True,
    "off": False, "false": False, "0": False,
}


def _validate(option: Any, value: str) -> tuple[str, str | None]:
    """Return (value_to_send, error). Keeps the option's own encoding."""
    option_type = int(getattr(option, "type", -1))
    elements = list(getattr(option, "elements", None) or [])

    if option_type == 0:
        truthy = _BOOL_WORDS.get(value.strip().lower())
        if truthy is None:
            return value, (
                f"{option.id!r}: {value!r} is not a recognised boolean. The backend would "
                f"accept it silently and turn the option OFF. Use one of "
                f"{'true/false' if option.value in ('true', 'false') else 'On/Off'}."
            )
        # Each Check option keeps its own spelling family; answer in the one it uses.
        if option.value in ("true", "false"):
            return ("true" if truthy else "false"), None
        return ("On" if truthy else "Off"), None

    if option_type in CHOICE_TYPES and elements:
        legal = [str(getattr(e, "id", "")) for e in elements]
        if value in legal:
            return value, None
        labels = ", ".join(
            f"{eid!r}" + (f" ({str(getattr(e, 'label', '') or '')})" if getattr(e, "label", "") else "")
            for eid, e in zip(legal, elements)
        )
        return value, f"{option.id!r}: {value!r} is not a legal value. Write an element id: {labels}."

    if option_type == 5 and elements:
        if len(value) != len(elements) or set(value) - {"0", "1"}:
            return value, (
                f"{option.id!r}: a CheckList value is one '0'/'1' character per element, in "
                f"element order ({len(elements)} characters here), not a list of ids. Got {value!r}."
            )
        return value, None

    return value, None


def _apply(method: str, ctx: dict[str, Any], cfg: str, options: list[dict[str, str]]) -> list[Any]:
    """Call Apply/Verify, retrying a Busy refusal.

    A human clicking around the IDE is enough to trigger Busy, so an un-retried
    write fails intermittently in ordinary use.
    """
    last: BusyError | None = None
    for attempt in range(BUSY_RETRIES):
        try:
            return get_client().project.call(method, ctx, cfg, options)
        except BusyError as exc:
            last = exc
            if attempt < BUSY_RETRIES - 1:
                time.sleep(BUSY_BACKOFF_S)
    raise last  # type: ignore[misc]


@mcp.tool()
def project_option_categories(project_path: str = "", config_name: str = "") -> dict[str, Any]:
    """List a configuration's option categories, with option counts.

    Args:
        project_path: Absolute .ewp path; empty = current project.
        config_name: Build configuration; empty = current one.
    """
    try:
        ctx, cfg = resolve_project(project_path, config_name)
        categories = _categories(ctx, cfg)
    except BusyError as exc:
        return envelope(ok=False, tool="project_option_categories", data={}, error=_busy(exc))
    return envelope(
        ok=True,
        tool="project_option_categories",
        data={
            "project": ctx,
            "configuration": cfg,
            "categories": [
                {"id": c.id, "option_count": len(c.optionIds)}
                for c in categories
            ],
        },
    )


@mcp.tool()
def project_get_options(
    project_path: str = "",
    config_name: str = "",
    category: str = "",
    ids: str = "",
    search: str = "",
    max_results: int = 60,
    include_elements: bool = True,
) -> dict[str, Any]:
    """Read project options and their legal values. Filter: a configuration has ~2000.

    `elements` lists the legal values; write back an element `id`. `Check`
    options are booleans ('On'/'Off' or 'true'/'false', per option).

    Args:
        project_path: Absolute .ewp path; empty = current project.
        config_name: Build configuration; empty = current one.
        category: Category id (see `project_option_categories`), exact or substring.
        ids: Comma-separated option ids, read instead of a category.
        search: Words that must all match id, title or description, ignoring
            case and punctuation ('runtime checking'). Product names like
            'C-RUN' are not indexed.
        max_results: Cap on returned options; `total_matched` has the full count.
        include_elements: Include legal values.
    """
    try:
        ctx, cfg = resolve_project(project_path, config_name)
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        category_id = None
        if not wanted and category:
            needle = category.strip().lower()
            categories = _categories(ctx, cfg)
            match = next((c for c in categories if c.id.lower() == needle), None)
            if match is None:
                match = next((c for c in categories if needle in c.id.lower()), None)
            if match is None:
                return envelope(
                    ok=False,
                    tool="project_get_options",
                    data={"categories": [c.id for c in categories]},
                    error=error_entry(
                        code="NO_SUCH_CATEGORY",
                        category="usage",
                        message=f"No option category matching {category!r}.",
                        retryable=False,
                        details={"hint": "Call project_option_categories() for the list."},
                    ),
                )
            category_id, wanted = match.id, list(match.optionIds)
        options = _read(ctx, cfg, wanted)
    except BusyError as exc:
        return envelope(ok=False, tool="project_get_options", data={}, error=_busy(exc))

    titles = _titles()
    shaped = [_option_data(o, titles, include_elements) for o in options]
    if search.strip():
        shaped = [
            o for o in shaped
            if _matches(search, o["id"], o["title"] or "", str(o.get("description", "")))
        ]
    total = len(shaped)
    limit = max(1, int(max_results))
    return envelope(
        ok=True,
        tool="project_get_options",
        data={
            "project": ctx,
            "configuration": cfg,
            "category": category_id,
            "total_matched": total,
            "truncated": total > limit,
            "options": shaped[:limit],
        },
    )


@mcp.tool()
def project_set_options(
    options_json: str,
    project_path: str = "",
    config_name: str = "",
    verify_only: bool = False,
) -> dict[str, Any]:
    """Set project options, validating each value first. Nothing is written unless all are valid.

    Values: an element `id` for List/Radio/DeviceSelection, 'On'/'Off' or
    'true'/'false' for Check, one '0'/'1' per element for CheckList. Read the
    option with `project_get_options` first. Set coupled options in one call.

    Args:
        options_json: JSON object of id -> value, or a list of {"id", "value"}.
        project_path: Absolute .ewp path; empty = current project.
        config_name: Build configuration; empty = current one.
        verify_only: Compute the result without changing the project.

    Returns:
        Per option: value before, requested and actually set. The change is
        in memory until the project is saved.
    """
    parsed = parse_json(options_json, "options_json")
    if isinstance(parsed, dict):
        requested = {str(k): str(v) for k, v in parsed.items()}
    elif isinstance(parsed, list):
        try:
            requested = {str(item["id"]): str(item["value"]) for item in parsed}
        except (TypeError, KeyError) as exc:
            raise CSpyError("options_json list items must be objects with 'id' and 'value'") from exc
    else:
        raise CSpyError("options_json must be a JSON object of id -> value, or a list of {id, value}")
    if not requested:
        raise CSpyError("options_json is empty: nothing to set")

    try:
        ctx, cfg = resolve_project(project_path, config_name)
        current = {o.id: o for o in _read(ctx, cfg, list(requested))}
    except BusyError as exc:
        return envelope(ok=False, tool="project_set_options", data={}, error=_busy(exc))

    unknown = [oid for oid in requested if oid not in current]
    payload: list[dict[str, str]] = []
    problems: list[str] = []
    for oid, value in requested.items():
        option = current.get(oid)
        if option is None:
            continue
        send, problem = _validate(option, value)
        if problem:
            problems.append(problem)
        else:
            payload.append({"id": oid, "value": send})

    if unknown:
        problems.append(
            f"Unknown option id(s): {', '.join(sorted(unknown))}. "
            "Ids differ between toolchains and versions - find them with project_get_options."
        )
    if problems:
        return envelope(
            ok=False,
            tool="project_set_options",
            data={"project": ctx, "configuration": cfg, "requested": requested},
            error=error_entry(
                code="INVALID_OPTION_VALUE",
                category="usage",
                message="; ".join(problems),
                retryable=False,
                details={"hint": "Read the option with project_get_options and write an element id."},
            ),
        )

    method = "VerifyOptionsForConfiguration" if verify_only else "ApplyOptionsForConfiguration"
    try:
        returned = _apply(method, ctx, cfg, payload)
    except BusyError as exc:
        return envelope(
            ok=False,
            tool="project_set_options",
            data={"project": ctx, "configuration": cfg, "requested": requested},
            error=_busy(exc),
        )

    actual = {o.id: o.value for o in returned}
    results = []
    for item in payload:
        oid = item["id"]
        before = current[oid].value
        got = actual.get(oid)
        results.append(
            {
                "id": oid,
                "before": before,
                "requested": item["value"],
                "actual": got,
                "changed": got is not None and got != before,
                "coerced": got is not None and got != item["value"],
            }
        )
    coerced = [r["id"] for r in results if r["coerced"]]
    return envelope(
        ok=True,
        tool="project_set_options",
        data={
            "project": ctx,
            "configuration": cfg,
            "verify_only": bool(verify_only),
            "persisted_to_disk": False,
            "coerced": coerced,
            "results": results,
        },
    )
