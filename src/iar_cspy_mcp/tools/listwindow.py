"""List window tools (trace and other tabular debugger views)."""

from __future__ import annotations

from typing import Any

from iar_cspy import UnsupportedMethod

from .._app import envelope, get_client, mcp, require_session


@mcp.tool()
def listwindow_list_services(name_filter: str = "listwindow") -> dict[str, Any]:
    """List services registered in ServiceRegistry, optionally filtered by name.

    Useful to discover listwindow-like services (for example trace/list windows)
    before querying rows. In standalone/headless sessions, instruction trace
    services may not be published: pass "" to see everything registered.

    `service_name` arguments on the other listwindow_* tools must be a `name`
    from this result's `services` list - never construct one. Under a normal
    backend it is a ServiceRegistry name; under a read-only IDE host (see
    `read_only` below) it is the window's own display name instead.

    `data.read_only` is true when the host serves list windows through a
    shared, read-only snapshot (an IDE-hosted session) rather than one
    `ListWindowBackend` per window: `listwindow_sliding_navigate` and the
    `listwindow_trace_*` tools are then unavailable (ok=False,
    error.code=UNSUPPORTED_METHOD).

    The default `name_filter="listwindow"` is special-cased to mean "no
    filter" (it matches every backend service name, but no IDE window name);
    any other value is a case-insensitive substring match against window
    names.
    """
    require_session("listwindow_list_services")
    data = {
        "read_only": get_client().listwindows.read_only(),
        "services": get_client().listwindows.services(name_filter),
    }
    return envelope(ok=True, tool="listwindow_list_services", data=data)


@mcp.tool()
def listwindow_get_overview(service_name: str) -> dict[str, Any]:
    """Fetch list window metadata such as display name, columns, and row count.

    `service_name` must be a `name` from listwindow_list_services's result.
    """
    require_session("listwindow_get_overview")
    overview = get_client().listwindows.window(service_name).overview()
    data = {"service_name": service_name, **overview.to_dict()}
    return envelope(ok=True, tool="listwindow_get_overview", data=data)


@mcp.tool()
def listwindow_get_rows(service_name: str, first_row: int = 0, max_rows: int = 50) -> dict[str, Any]:
    """Read rows from a list window.

    Args:
        service_name: A `name` from listwindow_list_services's result - never
            construct one.
        first_row: Start row index.
        max_rows: Maximum rows to fetch.
    """
    require_session("listwindow_get_rows")
    result = get_client().listwindows.window(service_name).rows(int(first_row), max(1, int(max_rows)))
    data = {
        "service_name": service_name,
        "is_sliding": result.is_sliding,
        "chunk_info": result.chunk_info,
        "row_count": result.row_count,
        "first_row": result.first_row,
        "returned": len(result.rows),
        "rows": result.rows,
    }
    return envelope(ok=True, tool="listwindow_get_rows", data=data)


@mcp.tool()
def listwindow_sliding_navigate(
    service_name: str,
    fraction: float = 0.5,
    chunk_pos: int = 0,
    min_lines: int = 100,
) -> dict[str, Any]:
    """Navigate a sliding listwindow to materialize chunk rows.

    Useful for trace/list windows that report zero rows until a chunk has been
    requested. Not available (ok=False, error.code=UNSUPPORTED_METHOD) when the
    host serves list windows read-only (see listwindow_list_services's
    `read_only`).
    """
    require_session("listwindow_sliding_navigate")
    data = {"service_name": service_name}
    try:
        chunk = get_client().listwindows.window(service_name).navigate(float(fraction), int(chunk_pos), int(min_lines))
    except UnsupportedMethod as exc:
        return envelope(ok=False, tool="listwindow_sliding_navigate", data=data, error=get_client().classify_error(exc))
    data.update(chunk.to_dict())
    return envelope(ok=True, tool="listwindow_sliding_navigate", data=data)


@mcp.tool()
def listwindow_get_notifications(clear: bool = False) -> dict[str, Any]:
    """Return captured ListWindowFrontend notifications from backend callbacks.

    Always empty when the host serves list windows read-only (see
    listwindow_list_services's `read_only`): there is no callback handshake
    in that mode, not an error.
    """
    require_session("listwindow_get_notifications")
    notes, toolbar_notes = get_client().listwindows.notifications(clear=bool(clear))
    data = {
        "notes": notes,
        "toolbar_notes": toolbar_notes,
        "note_count": len(notes),
        "toolbar_note_count": len(toolbar_notes),
    }
    return envelope(ok=True, tool="listwindow_get_notifications", data=data)


@mcp.tool()
def listwindow_trace_status(service_name: str) -> dict[str, Any]:
    """Get trace-window capabilities and enablement state for a trace listwindow.

    Not available (ok=False, error.code=UNSUPPORTED_METHOD) when the host
    serves list windows read-only (see listwindow_list_services's `read_only`).
    """
    require_session("listwindow_trace_status")
    data = {"service_name": service_name}
    try:
        status = get_client().listwindows.trace(service_name).status()
    except UnsupportedMethod as exc:
        return envelope(ok=False, tool="listwindow_trace_status", data=data, error=get_client().classify_error(exc))
    data.update(status.to_dict())
    return envelope(ok=True, tool="listwindow_trace_status", data=data)


@mcp.tool()
def listwindow_trace_set_enabled(service_name: str, enabled: bool = True) -> dict[str, Any]:
    """Enable or disable tracing for a list window.

    Not available (ok=False, error.code=UNSUPPORTED_METHOD) when the host
    serves list windows read-only (see listwindow_list_services's `read_only`).
    """
    require_session("listwindow_trace_set_enabled")
    data = {"service_name": service_name}
    try:
        state = get_client().listwindows.trace(service_name).set_enabled(bool(enabled))
    except UnsupportedMethod as exc:
        return envelope(ok=False, tool="listwindow_trace_set_enabled", data=data, error=get_client().classify_error(exc))
    data["enabled"] = state
    return envelope(ok=True, tool="listwindow_trace_set_enabled", data=data)


@mcp.tool()
def listwindow_trace_clear(service_name: str) -> dict[str, Any]:
    """Clear trace data for a list window, when supported.

    Not available (ok=False, error.code=UNSUPPORTED_METHOD) when the host
    serves list windows read-only (see listwindow_list_services's `read_only`).
    """
    require_session("listwindow_trace_clear")
    data = {"service_name": service_name}
    try:
        get_client().listwindows.trace(service_name).clear()
    except UnsupportedMethod as exc:
        return envelope(ok=False, tool="listwindow_trace_clear", data=data, error=get_client().classify_error(exc))
    return envelope(ok=True, tool="listwindow_trace_clear", data=data)
