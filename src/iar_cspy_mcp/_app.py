"""The FastMCP application and the one iar_cspy Client every tool uses."""

from __future__ import annotations

import functools
import os
import threading
from typing import Any, Callable

from iar_cspy import BackendUnavailableError, Client, Config, SessionError
from mcp.server.fastmcp import FastMCP

from ._connection_trouble import clarify


def _clarify_unavailable_backend(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Give a tool's ``BackendUnavailableError`` the directive text an AI caller needs.

    Every other error passes through unchanged. The error keeps its class,
    ``kind`` and ``reason``; only its hint is replaced (see
    :mod:`._connection_trouble`).
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except BackendUnavailableError as exc:
            raise clarify(exc, registry_uri=get_client().config.registry_uri) from exc

    return wrapper


class _Server(FastMCP):
    """FastMCP whose tools all report an unavailable backend the same way."""

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        register = super().tool(*args, **kwargs)
        return lambda fn: register(_clarify_unavailable_backend(fn))


mcp = _Server(
    "thrift-debugger-server",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)

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
