"""The FastMCP application and the one iar_cspy Client every tool uses."""

from __future__ import annotations

import functools
import os
import threading
from typing import Any, Callable

from iar_cspy import Client, Config, CSpyError, NotConnectedError, SessionError
from mcp.server.fastmcp import FastMCP

from ._connection_trouble import connection_hint

mcp = FastMCP(
    "thrift-debugger-server",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)


def _clarify_connection_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Rewrite a dead/unreachable-backend message into a directive one.

    A raised ``CSpyError`` is normally the text an AI caller sees verbatim, and
    "Could not connect to ('127.0.0.1', 40557)" reads like something to go
    investigate. It is not: see :mod:`._connection_trouble`. Every other
    ``CSpyError`` (including ``BusyError``, which callers match on by type)
    passes through completely unchanged.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except CSpyError as exc:
            client = get_client()
            hint = connection_hint(exc, mode=client.backend.mode, registry_uri=client.config.registry_uri)
            if hint is None:
                raise
            # Every exception that can actually reach here - plain CSpyError
            # (ServiceRegistry._endpoint, a transport failure) or
            # NotConnectedError - takes exactly (message, *, diagnostics=""),
            # so the type is preserved for callers matching on it by isinstance.
            cls = type(exc) if type(exc) in (CSpyError, NotConnectedError) else CSpyError
            raise cls(f"{exc}\n\n{hint}", diagnostics=getattr(exc, "diagnostics", "")) from exc

    return wrapper


# Every `@mcp.tool()` below this point is wrapped with `_clarify_connection_errors`
# first, so individual tool modules need no changes to get this.
_register_tool = mcp.tool


def _tool_with_clarified_errors(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    register = _register_tool(*args, **kwargs)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        return register(_clarify_connection_errors(fn))

    return decorator


mcp.tool = _tool_with_clarified_errors  # type: ignore[method-assign]

_lock = threading.Lock()
_client: Client | None = None


def get_client() -> Client:
    """The server's client, created from the environment on first use."""
    global _client
    with _lock:
        if _client is None:
            _client = Client(Config.from_env(), owns_backend=True)
        return _client


def set_client(client: Client | None) -> None:
    """Install the client the tools use (the CLI and tests do this)."""
    global _client
    with _lock:
        _client = client


def require_session(tool_name: str) -> None:
    """The lifecycle guard for tools that need a started debug session."""
    if not get_client().debugger.started:
        raise SessionError(
            f"{tool_name} requires an active debug session. Required order: "
            "debugger_configure_session(launch_json) -> debugger_start_smp_session()."
        )


def envelope(*, ok: bool, data: Any, tool: str, error: dict[str, Any] | None = None) -> dict[str, Any]:
    """The stable response shape of the AI-first tools."""
    return {"ok": bool(ok), "tool": tool, "data": data, "error": error}
