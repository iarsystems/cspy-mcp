"""The IDE's logs: Build, Debug Log, Tool Output, ... (``client.logs``).

Not session-scoped: the IDE logs whether or not a debug session runs. The
logs come from the IDE platform's log forwarder, which posts to a
``LogService`` this server hosts; nothing logged before that service was
registered is replayed, so the server registers it at startup and again
before every IDE service call (``client.logs.auto_ensure``).

The debugger's own log is category ``CSpy_Debug``: the log events the
debugger posts to this server, also without an IDE (``debugger_get_log``
reads it too). It needs no forwarder, so it can be read while not listening.

Reading pages by stream position: every entry has a ``seq`` that never
changes, and every result says where it is (``page``). Pass ``page.last_seq``
as ``after`` for what came next, ``page.first_seq`` as ``before`` for what came
before. Positions stay valid while the log grows, unlike offsets.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from iar_cspy import CSpyError
from iar_cspy.errors import error_entry
from iar_cspy.logs import DEBUGGER_LOG, PROBLEM_SEVERITIES, SEVERITY_PREFIXES
from iar_cspy.types import LogEntry, LogSeverity

from .._app import envelope, get_client, mcp

#: The most a page of text may hold; long lines end a page early (``truncated``).
MAX_PAGE_CHARS = 20000
MAX_LIMIT = 500
#: An MCP call has to return before the client gives up on it.
MAX_WAIT_MS = 60000


def _listening() -> dict[str, Any]:
    """Register (best effort) and say whether logs can arrive, and if not why."""
    client = get_client()
    logs = client.logs
    logs.auto_ensure()
    status = logs.status()
    out: dict[str, Any] = {
        "listening": bool(status["registered"]),
        "listening_since": status["listening_since"],
        "forwarder": status["forwarder"],
    }
    if not out["listening"]:
        if not client.config.auto_log_service:
            out["note"] = "Disabled by THRIFT_AUTO_LOG_SERVICE=0."
        else:
            out["note"] = "No service registry reachable yet; start the backend or the IDE (remote control on)."
    elif client.backend.mode == "managed" and not client.backend.has_launcher:
        out["note"] = (
            "This backend is CSpyServer2 only and has no log forwarder, so no IDE logs arrive. "
            f"The debug log is category {DEBUGGER_LOG}."
        )
    elif isinstance(status["forwarder"], dict) and status["forwarder"].get("status") == "error":
        out["note"] = "The log forwarder failed to load into the IarServiceLauncher; see forwarder.error."
    return out


def _readable(listening: dict[str, Any], category: str | None) -> bool:
    """Whether there is anything to read: the debugger's log needs no forwarder."""
    return bool(listening["listening"]) or category in (None, DEBUGGER_LOG)


def _debug_log_category(summary: Any) -> str | None:
    """Of Log_Debug (an IDE's) and CSpy_Debug (the debugger's), the one with the newest entry."""
    found = [c for c in summary.categories if c.name in ("Log_Debug", DEBUGGER_LOG) and c.last_seq is not None]
    return max(found, key=lambda c: c.last_seq).name if found else None


def _unavailable(tool: str, listening: dict[str, Any]) -> dict[str, Any]:
    error = error_entry(
        code="LOGS_UNAVAILABLE",
        category="logs",
        message=f"Not listening to the IDE's logs. {listening.get('note', '')}".strip(),
        retryable=True,
    )
    return envelope(ok=False, tool=tool, data=listening, error=error)


def _bad_args(tool: str, exc: CSpyError) -> dict[str, Any]:
    error = error_entry(code="INVALID_ARGUMENT", category="arguments", message=str(exc), retryable=False)
    return envelope(ok=False, tool=tool, data={}, error=error)


def _session(value: str | int | None) -> int | str | None:
    """``"current"``, ``"all"`` (None) or a session number, as MCP clients send it."""
    if value is None or value == "all":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in ("", "all"):
        return None
    if text == "current":
        return "current"
    try:
        return int(text)
    except ValueError:
        raise CSpyError(f"session must be 'current', 'all' or a session number, not {value!r}") from None


def _match(match: str | None, regex: bool) -> str | re.Pattern[str] | None:
    if not match:
        return None
    if not regex:
        return match
    try:
        return re.compile(match, re.IGNORECASE)
    except re.error as exc:
        raise CSpyError(f"Invalid regex in match: {exc}") from exc


def _int_or_none(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CSpyError(f"{name} must be a seq number (page.first_seq/last_seq), not {value!r}") from None


def format_line(entry: LogEntry, with_category: bool) -> str:
    """One entry as one line: as the IDE's window shows it, plus where, when the text does not say."""
    text = entry.text.rstrip("\n")
    severity = SEVERITY_PREFIXES.get(entry.severity, "")
    if severity and text.lower().startswith(severity.split(":")[0].lower()):
        severity = ""  # the text says it already: "Error[Pe020]: ..."
    prefix = "  " if entry.is_sub_entry else ""
    if with_category:
        prefix += f"[{entry.category}] "
    if entry.path and os.path.basename(entry.path) not in text:
        where = entry.path if entry.line is None else f"{entry.path}:{entry.line}"
        if entry.line is not None and entry.column:
            where += f":{entry.column}"
        # "path:line:col: Error: text" reads like a compiler's diagnostic.
        return f"{prefix}{where}: {severity}{text}"
    return prefix + severity + text


def _shape_page(page: Any, fmt: str, with_category: bool) -> dict[str, Any]:
    """The page as MCP output, cut short when the text would get too long."""
    items: list[Any] = []
    used = 0
    kept: list[LogEntry] = []
    for entry in page.entries:
        item: Any = format_line(entry, with_category) if fmt == "text" else entry.to_dict()
        size = len(item) if isinstance(item, str) else len(str(item))
        if kept and used + size > MAX_PAGE_CHARS:
            break
        used += size + 1
        items.append(item)
        kept.append(entry)
    truncated = len(kept) < len(page.entries)
    info = {
        "returned": len(kept),
        "total": page.total,
        "before": page.before,
        "after": page.after + (len(page.entries) - len(kept)),
        "first_seq": kept[0].seq if kept else page.first_seq,
        "last_seq": kept[-1].seq if kept else page.last_seq,
        "truncated": truncated,
    }
    out: dict[str, Any] = {"page": info}
    if fmt == "text":
        out["text"] = "\n".join(items)
    else:
        out["entries"] = items
    return out


@mcp.tool()
def logs_status() -> dict[str, Any]:
    """Whether this server receives the IDE's logs, and what each log holds.

    Each category is one of the IDE's logs (Log_Build, Log_Debug,
    Log_Tool_Output, ...), or CSpy_Debug, the debugger's own log, with: open (the IDE has it now), session (bumped
    each time the IDE restarts that log, e.g. a new build), entries stored,
    errors and warnings in the current session, and last_seq. `mark` is the
    stream position now: logs_read(after=mark) returns only what comes later.

    `debug_log` names the category with the debug log: Log_Debug when an IDE
    runs the session, CSpy_Debug otherwise (whichever has the newest entry).

    Nothing logged before `listening_since` can be read: the IDE does not
    replay its logs.
    """
    listening = _listening()
    summary = get_client().logs.summary()
    data = {**listening, **summary.to_dict(), "debug_log": _debug_log_category(summary)}
    return envelope(ok=True, tool="logs_status", data=data)


@mcp.tool()
def logs_read(
    category: str | None = None,
    session: str | int | None = "current",
    problems: bool = False,
    min_severity: str | None = None,
    match: str | None = None,
    regex: bool = False,
    after: int | None = None,
    before: int | None = None,
    limit: int = 100,
    format: str = "text",
) -> dict[str, Any]:
    """Read the IDE's logs, a page at a time, oldest first within the page.

    Examples:
        the last build's errors and warnings: logs_read("Log_Build", problems=true)
        the newest debug log lines: logs_read("Log_Debug")
        what is new since last time: logs_read(after=<previous page.last_seq>)

    Args:
        category: One log (Log_Build, Log_Debug, ...; see logs_status), or all.
            CSpy_Debug is the debugger's own log, also without an IDE.
            Without one, each line starts with its [category].
        session: "current" (each log since the IDE last restarted it), "all",
            or a session number.
        problems: Only warnings and errors (WARNING, ERROR), with their
            detail lines.
        min_severity: DEBUG, USER, MINOR_INFO, INFO, WARNING, ERROR, ALERT,
            SUPER. This ranks how prominently the IDE shows a line, not how
            bad it is: ALERT and SUPER are highlighted messages ("Build
            succeeded", headlines), not errors. For problems use problems=true.
            The detail lines that follow a kept entry are kept too.
        match: Keep entries whose text contains this (case-insensitive), or
            matches it as a regex with regex=true.
        after: Entries after this seq, from the oldest: page forwards, or
            follow a log. Pass the previous page.last_seq.
        before: Entries before this seq, the newest of them: page backwards.
            Pass the previous page.first_seq.
        limit: Entries per page (at most 500). Without after/before: the
            newest `limit` entries.
        format: "text" (one line per entry, warnings/errors prefixed, detail
            lines indented) or "entries" (path, line, column, severity, ...).

    `data.page` says where the page is: total matching, how many matching
    entries come before and after it, and first_seq/last_seq. after == 0
    means nothing newer. A page with very long lines can end early
    (truncated); the next page starts where it stopped.
    """
    tool = "logs_read"
    listening = _listening()
    if not _readable(listening, category or None):
        return _unavailable(tool, listening)
    fmt = str(format).strip().lower()
    try:
        if fmt not in ("text", "entries"):
            raise CSpyError(f"format must be 'text' or 'entries', not {format!r}")
        page = get_client().logs.page(
            category or None,
            session=_session(session),
            min_severity=None if not min_severity else _severity(min_severity),
            severities=PROBLEM_SEVERITIES if problems else None,
            match=_match(match, regex),
            after=_int_or_none(after, "after"),
            before=_int_or_none(before, "before"),
            limit=max(1, min(int(limit), MAX_LIMIT)),
        )
    except CSpyError as exc:
        return _bad_args(tool, exc)
    data = _shape_page(page, fmt, with_category=not category)
    data["listening_since"] = listening["listening_since"]
    if not listening["listening"]:
        data["note"] = f"Only the debugger's own log ({DEBUGGER_LOG}): {listening.get('note', '')}".strip()
    return envelope(ok=True, tool=tool, data=data)


@mcp.tool()
def logs_wait_for(
    match: str | None = None,
    category: str | None = None,
    min_severity: str | None = None,
    regex: bool = False,
    after: int | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Wait until the IDE logs an entry that matches, e.g. a target printf or the end of a download.

    Args:
        match: Text the entry contains (case-insensitive), or a regex with
            regex=true. Empty matches any entry.
        category: Only this log (Log_Build, Log_Debug, ...).
        min_severity: Only entries at least this prominent (see logs_read).
        after: Look from this seq on, so an entry logged before the call is
            found too. Default: only entries logged from now on.
        timeout_ms: At most 60000.

    `data.found` is false on timeout; that is not an error. `data.cursor` is
    the position to pass as `after` to logs_read or to the next wait.
    """
    tool = "logs_wait_for"
    listening = _listening()
    if not _readable(listening, category or None):
        return _unavailable(tool, listening)
    logs = get_client().logs
    try:
        pattern = _match(match, regex)
        floor = None if not min_severity else _severity(min_severity)
        start = _int_or_none(after, "after")
    except CSpyError as exc:
        return _bad_args(tool, exc)
    if start is None:
        start = logs.mark()

    def wanted(entry: LogEntry) -> bool:
        if category and entry.category != category:
            return False
        if floor is not None and entry.severity < floor:
            return False
        if pattern is None:
            return True
        if isinstance(pattern, str):
            return pattern.lower() in entry.text.lower()
        return pattern.search(entry.text) is not None

    timeout = max(0, min(int(timeout_ms), MAX_WAIT_MS)) / 1000.0
    entry = logs.wait_for(wanted, after=start, timeout=timeout)
    if entry is None:
        data = {"found": False, "cursor": logs.mark(), "timeout_ms": int(timeout * 1000)}
    else:
        data = {"found": True, "cursor": entry.seq, "line": format_line(entry, True), "entry": entry.to_dict()}
    return envelope(ok=True, tool=tool, data=data)


def _severity(value: str) -> LogSeverity:
    try:
        return LogSeverity[str(value).strip().upper()]
    except KeyError:
        names = ", ".join(s.name for s in LogSeverity)
        raise CSpyError(f"Unknown log severity {value!r}; known: {names}") from None


def build_problems(after: int, limit: int = 50) -> dict[str, Any] | None:
    """Warnings and errors the IDE logged in Log_Build after ``after``, for project_build.

    None when this server does not receive the IDE's logs.
    """
    logs = get_client().logs
    if not logs.status()["registered"]:
        return None
    _settle(logs, after)
    page = logs.page("Log_Build", after=after, severities=PROBLEM_SEVERITIES)
    main = [e for e in page.entries if not e.is_sub_entry]
    shown = page.entries[:limit]
    return {
        "errors": sum(1 for e in main if e.severity == LogSeverity.ERROR),
        "warnings": sum(1 for e in main if e.severity == LogSeverity.WARNING),
        "lines": [format_line(e, False) for e in shown],
        "truncated": len(shown) < len(page.entries),
        "after": after,
        "cursor": logs.mark(),
    }


def _settle(logs: Any, after: int, quiet_s: float = 0.2, max_s: float = 2.0) -> None:
    """Wait for the forwarder to deliver the last of the build's entries.

    The forwarder posts asynchronously, so the build call can return before
    its last lines arrive: wait until the log is quiet for ``quiet_s``.
    """
    deadline = time.monotonic() + max_s
    position = max(after, logs.mark())
    while time.monotonic() < deadline:
        if logs.wait_for(lambda _e: True, after=position, timeout=quiet_s) is None:
            return
        position = logs.mark()
