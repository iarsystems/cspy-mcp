"""Code coverage tools: switch coverage on, reset it, and read the report.

Session-scoped: every tool requires a started debug session
(``require_session``), and the backend offers coverage only once the program
is loaded and only for drivers that support it (see coverage_get_status).
Failures are enveloped, like crun.py. Docs: docs/coverage.md.

Counts, as the backend reports them: a program, module or function has
``total_count`` steps (statements, or parts of them), of which ``exec_count``
executed fully. A step's own counts are of the finer machine-code units it is
made of, so a step can be partly executed (a branch taken only one way); the
step rows carry that as ``status``.
"""

from __future__ import annotations

from typing import Any

from iar_cspy import CoverageLocation, CoverageReport, CSpyError

from .._app import envelope, get_client, mcp, require_session

LEVELS = ("module", "function", "step")


def _coverage_error(tool: str, data: dict[str, Any], exc: CSpyError) -> dict[str, Any]:
    return envelope(ok=False, tool=tool, data=data, error=get_client().classify_error(exc))


def _percent(exec_count: int, total_count: int) -> float | None:
    return round(100.0 * exec_count / total_count, 1) if total_count else None


def _counts(exec_count: int, total_count: int) -> dict[str, Any]:
    return {"exec_count": exec_count, "total_count": total_count, "percent": _percent(exec_count, total_count)}


def _step_status(exec_count: int, total_count: int) -> str:
    if exec_count <= 0:
        return "not_executed"
    return "executed" if exec_count >= total_count else "partial"


def _where(location: CoverageLocation | None) -> dict[str, Any]:
    if location is None:
        return {"file": None, "line": None}
    return {"file": location.file, "line": location.start_line}


def _items(report: CoverageReport, level: str) -> list[dict[str, Any]]:
    """The report flattened to one row per item of `level`, outermost first."""
    rows: list[dict[str, Any]] = []
    for module in report.modules:
        if level == "module":
            rows.append({"module": module.name, **_counts(module.exec_count, module.total_count)})
            continue
        for function in module.functions:
            if level == "function":
                rows.append(
                    {
                        "module": module.name,
                        "function": function.name,
                        **_where(function.location),
                        **_counts(function.exec_count, function.total_count),
                    }
                )
                continue
            for step in function.steps:
                loc = step.location
                rows.append(
                    {
                        "module": module.name,
                        "function": function.name,
                        "code": step.code,
                        "file": loc.file if loc else None,
                        "start_line": loc.start_line if loc else None,
                        "start_col": loc.start_col if loc else None,
                        "end_line": loc.end_line if loc else None,
                        "end_col": loc.end_col if loc else None,
                        "status": _step_status(step.exec_count, step.total_count),
                        "exec_count": step.exec_count,
                        "total_count": step.total_count,
                    }
                )
    return rows


def _matches(row: dict[str, Any], needle: str) -> bool:
    fields = (row.get("module"), row.get("function"), row.get("file"))
    return any(needle in str(value).lower() for value in fields if value)


@mcp.tool()
def coverage_get_status() -> dict[str, Any]:
    """Whether code coverage is supported, enabled and initialized in this session.

    `supported` false means the driver has no code coverage. `enabled` must be
    true while the target runs for executions to be counted.
    `initialized` turns true with the first report.
    `session_id` tells reports of different sessions apart.
    """
    require_session("coverage_get_status")
    coverage = get_client().coverage
    try:
        supported = coverage.has_metadata_support()
        data: dict[str, Any] = {"supported": supported}
        if supported:
            data.update(
                enabled=coverage.is_enabled(),
                initialized=coverage.is_initialized(),
                session_id=coverage.session_id(),
            )
    except CSpyError as exc:
        return _coverage_error("coverage_get_status", {}, exc)
    return envelope(ok=True, tool="coverage_get_status", data=data)


@mcp.tool()
def coverage_set_enabled(enabled: bool = True) -> dict[str, Any]:
    """Switch code coverage collection on (default) or off.

    Switch it on before running the target: only executions while it is on are
    counted. Switching it off keeps what was collected. The backend saves the
    setting with the session's settings, so later sessions of the same program
    start with it.
    """
    require_session("coverage_set_enabled")
    data = {"enabled": bool(enabled)}
    coverage = get_client().coverage
    try:
        coverage.enable() if enabled else coverage.disable()
    except CSpyError as exc:
        return _coverage_error("coverage_set_enabled", data, exc)
    return envelope(ok=True, tool="coverage_set_enabled", data=data)


@mcp.tool()
def coverage_reset() -> dict[str, Any]:
    """Discard the coverage collected so far, to count again from zero."""
    require_session("coverage_reset")
    try:
        get_client().coverage.reset()
    except CSpyError as exc:
        return _coverage_error("coverage_reset", {}, exc)
    return envelope(ok=True, tool="coverage_reset", data={})


@mcp.tool()
def coverage_get_report(
    level: str = "function",
    match: str = "",
    uncovered_only: bool = False,
    max_items: int = 200,
    refresh: bool = True,
) -> dict[str, Any]:
    """Read the code coverage of the program, per module, function or step.

    Enable coverage (coverage_set_enabled) before running the target.
    `data.program` has the totals: `exec_count` of its `total_count` steps
    (statements, or parts of them) fully executed, and `percent`.
    `data.items` lists the items of `level`:
    - "module": `module` and its step counts.
    - "function": `module`, `function`, `file`, `line` (where it starts) and its step counts.
    - "step": one statement or part of one: `module`, `function`, `code` (its
      source text), `file`, `start_line`/`start_col`/`end_line`/`end_col`, and
      `status`: "executed", "partial" (e.g. a branch taken only one way) or
      "not_executed". Its `exec_count`/`total_count` are of the machine-code
      units it is made of. With `uncovered_only` and `match`, this finds the
      code in a file that has not (fully) run.

    Args:
        level: "module", "function" (default) or "step".
        match: Keep only items whose module, function or source file contains
            this text (case-insensitive). Empty keeps all.
        uncovered_only: Keep only items not fully executed (exec_count < total_count;
            for steps, status "partial" or "not_executed").
        max_items: Most items to return; `data.truncated` is true when more matched.
        refresh: Read the latest counts from the target first (default true).
    """
    require_session("coverage_get_report")
    data: dict[str, Any] = {"level": level, "match": match, "uncovered_only": bool(uncovered_only)}
    if level not in LEVELS:
        bad_level = CSpyError(f"level must be one of {', '.join(LEVELS)}, not {level!r}.")
        return _coverage_error("coverage_get_report", data, bad_level)
    try:
        report = get_client().coverage.report(refresh=bool(refresh))
    except CSpyError as exc:
        return _coverage_error("coverage_get_report", data, exc)

    rows = _items(report, level)
    needle = match.strip().lower()
    if needle:
        rows = [row for row in rows if _matches(row, needle)]
    if uncovered_only:
        rows = [row for row in rows if row["exec_count"] < row["total_count"]]
    limit = max(1, int(max_items))
    data.update(
        program={"name": report.program, **_counts(report.exec_count, report.total_count)},
        count=len(rows),
        truncated=len(rows) > limit,
        items=rows[:limit],
    )
    return envelope(ok=True, tool="coverage_get_report", data=data)


@mcp.tool()
def coverage_get_xml(max_chars: int = 200_000, refresh: bool = True) -> dict[str, Any]:
    """The coverage report as the backend's raw `<cpMetaDataInfo>` XML, for export.

    Prefer coverage_get_report to read it. `data.truncated` is true when the
    XML is longer than `max_chars`; `data.length` is its full length.
    """
    require_session("coverage_get_xml")
    try:
        xml = get_client().coverage.xml(refresh=bool(refresh))
    except CSpyError as exc:
        return _coverage_error("coverage_get_xml", {}, exc)
    limit = max(1, int(max_chars))
    data = {"length": len(xml), "truncated": len(xml) > limit, "xml": xml[:limit]}
    return envelope(ok=True, tool="coverage_get_xml", data=data)
