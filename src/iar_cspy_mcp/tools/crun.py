"""C-RUN tools: rules, the default action, and the live runtime-check messages.

Session-scoped, like breakpoints/memory: every tool requires a started debug
session (``require_session``). Failures are enveloped, like project.py and
options.py, with ``CRUN_*`` codes where there is one (see
``iar_cspy.errors.ERROR_CODES``). Docs: docs/crun.md.
"""

from __future__ import annotations

from typing import Any

from iar_cspy import CSpyError

from .._app import envelope, get_client, mcp, require_session

#: The IDE's C-RUN Message Rules list window.
RULES_WINDOW = "WIN_C_RUN_MESSAGE_RULES"


def _crun_error(tool: str, data: dict[str, Any], exc: CSpyError) -> dict[str, Any]:
    return envelope(ok=False, tool=tool, data=data, error=get_client().classify_error(exc))


@mcp.tool()
def crun_get_default_action() -> dict[str, Any]:
    """Get the default (catch-all) C-RUN rule's action: "STOP_AND_LOG", "LOG" or "IGNORE"."""
    require_session("crun_get_default_action")
    try:
        action = get_client().crun_rules.default_action()
    except CSpyError as exc:
        return _crun_error("crun_get_default_action", {}, exc)
    return envelope(ok=True, tool="crun_get_default_action", data={"action": action.name})


@mcp.tool()
def crun_set_default_action(action: str) -> dict[str, Any]:
    """Set the default (catch-all) C-RUN rule's action.

    Args:
        action: "STOP_AND_LOG", "LOG", or "IGNORE".
    """
    require_session("crun_set_default_action")
    data = {"action": action}
    try:
        get_client().crun_rules.set_default_action(action)
    except CSpyError as exc:
        return _crun_error("crun_set_default_action", data, exc)
    return envelope(ok=True, tool="crun_set_default_action", data=data)


@mcp.tool()
def crun_list_rules() -> dict[str, Any]:
    """List the C-RUN rules as the IDE's C-RUN Message Rules window shows them.

    Each rule has `index` and `cells`, keyed by the window's column titles
    (Check, Source File, Action). The last rule is the default one.
    """
    require_session("crun_list_rules")
    try:
        window = get_client().listwindows.window(RULES_WINDOW)
        titles = [str(col.get("title", "")) for col in window.overview().columns or []]
        result = window.rows()
    except CSpyError as exc:
        return _crun_error("crun_list_rules", {}, exc)
    rules = []
    for item in result.rows:
        texts = [str(cell.get("text", "")) for cell in item["row"].get("cells", [])]
        rules.append({"index": item["index"], "cells": dict(zip(titles, texts))})
    return envelope(ok=True, tool="crun_list_rules", data={"rules": rules, "count": len(rules)})


@mcp.tool()
def crun_add_rule(
    message_pattern: str,
    file: str | None = None,
    start_line: int | None = None,
    start_col: int | None = None,
    end_line: int | None = None,
    end_col: int | None = None,
) -> dict[str, Any]:
    """Add a C-RUN rule ahead of the existing ones, for messages matching `message_pattern`.

    With no location it matches anywhere; with `file` alone, anywhere in that
    file; with a line/col too (`file` required), in that source range
    (`end_line`/`end_col` default to `start_line`/`start_col`, for a
    single-point rule). The new rule's action is "LOG"; see crun_list_rules.
    """
    require_session("crun_add_rule")
    data = {
        "message_pattern": message_pattern,
        "file": file,
        "start_line": start_line,
        "start_col": start_col,
        "end_line": end_line,
        "end_col": end_col,
    }
    try:
        get_client().crun_rules.add_rule(
            message_pattern,
            file=file,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
        )
    except CSpyError as exc:
        return _crun_error("crun_add_rule", data, exc)
    return envelope(ok=True, tool="crun_add_rule", data=data)


@mcp.tool()
def crun_get_messages(namespace: str | None = None, max_messages: int = 200) -> dict[str, Any]:
    """Read the live C-RUN runtime-check messages the backend has pushed (CRunDisplay).

    Registers this server as the backend's `crun.display` on first use. Only
    messages pushed after that are seen: call it before running the target. If
    something else already registered `crun.display` (an IDE's own C-RUN
    Messages window), nothing reaches this server and `data.receiving` is false;
    read that window with listwindow_get_rows("WIN_C_RUN_MESSAGES") instead.

    Messages are kept per `namespace` (the backend partner that sent them; ""
    for an IDE, which hosts every core itself). Pass one to see only its
    messages. Each message has `name`, `text`, `core`, `cycle`, `repeat_count`,
    `call_stack`, `break_action` and nested `sub_messages`. `data.truncated` is
    true when more than `max_messages` matched.

    A backend's `updateAll` push empties its namespace until the pushes that
    follow refill it.
    """
    messages = get_client().crun_messages
    try:
        registration = messages.ensure_registered()
    except CSpyError as exc:
        return _crun_error("crun_get_messages", {}, exc)
    stored = messages.all_messages() if namespace is None else {namespace: messages.messages(namespace)}
    limit = max(1, int(max_messages))
    flat = [(ns, m) for ns, items in sorted(stored.items()) for m in items]
    data = {
        "receiving": messages.receiving,
        "registration": registration,
        "namespaces": messages.namespaces(),
        "count": len(flat),
        "truncated": len(flat) > limit,
        "messages": [{"namespace": ns, **m.to_dict()} for ns, m in flat[:limit]],
    }
    return envelope(ok=True, tool="crun_get_messages", data=data)


@mcp.tool()
def crun_clear_messages(namespace: str | None = None) -> dict[str, Any]:
    """Forget the stored C-RUN messages of `namespace`, or of every namespace."""
    get_client().crun_messages.clear(namespace)
    return envelope(ok=True, tool="crun_clear_messages", data={"namespace": namespace})
