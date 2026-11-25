"""What an AI caller should do when the backend is missing or gone.

``iar_cspy`` classifies the failure (``BackendUnavailableError.kind``) and
describes it briefly. A raw "Could not connect to ('127.0.0.1', 40557)" invites
exactly the wrong response, though: hunting for a PID, an old port or a
coredump. By the time this fires, there is nothing left at that address, and
the explanation would not change the next step. This module only supplies the
directive text for each kind; it does not look at error messages.
"""

from __future__ import annotations

from typing import Any

from iar_cspy import BackendUnavailableError
from iar_cspy.errors import DISCOVERED_GONE, FIXED_UNREACHABLE, MANAGED_EXITED, NEVER_FOUND

#: Status-tool fields holding the text of a probe that failed instead of raising.
#: ``iar_cspy`` adds ``<field>_kind`` and ``<field>_hint`` next to them.
STATUS_ERROR_FIELDS = ("backend_online_error", "core_state_error", "registry_error")


def guidance(kind: str, *, registry_uri: str | None = None) -> str | None:
    """The directive explanation for a ``BackendUnavailableError.kind``, or None if unknown."""
    if kind == NEVER_FOUND:
        return (
            "No IAR backend is reachable right now, and there has never been one "
            "found to reconnect to. Do not go looking for a process, a port, or a "
            "*.remote discovery file - there is nothing to find. Start an IAR IDE "
            "(or CSpyServer2/IarServiceLauncher) for this server to discover, or "
            "ask the user to, then retry this same call."
        )
    if kind == MANAGED_EXITED:
        return (
            "The backend process this server started has exited. That process is "
            "gone - do not search for its old PID or port, and do not go hunting for "
            "a coredump or crash log to explain it; this server does not control "
            "what the IDE does before exiting, and the explanation would not change "
            "what to do next. The next call restarts it automatically: simply "
            "retry. A debug session it held is gone, so configure and start it "
            "again. If it keeps failing, ask the user whether to restart the MCP "
            "server itself."
        )
    if kind == FIXED_UNREACHABLE:
        return (
            f"The configured backend at {registry_uri!r} is no longer reachable, and "
            "this server has no way to restart a process it does not own. Do not "
            "search for its PID or an old port: ask the user to restart that "
            "backend/IDE, or reconfigure this server (--auto-discovery, or a "
            "different registry) to find another one."
        )
    if kind == DISCOVERED_GONE:
        return (
            "The previously-connected IAR IDE process is gone. Do not search for "
            "its PID, its old port, or stale *.remote discovery files - there is "
            "nothing left at that address, and digging for a root cause (a "
            "coredump, a crash log) will not change what to do next; this server "
            "has no visibility into why the IDE exited. Auto-discovery will pick up "
            "a new IDE the next time one registers: give up on this session, wait, "
            "and retry the same call later. A debug session it hosted is gone. If "
            "none is expected to start on its own, ask the user to start or restart "
            "their IAR IDE."
        )
    return None


def clarify(exc: BackendUnavailableError, *, registry_uri: str | None) -> BackendUnavailableError:
    """``exc`` with the directive text in place of its brief hint (same class, kind and reason)."""
    text = guidance(exc.kind, registry_uri=registry_uri)
    if text is None:
        return exc
    return BackendUnavailableError(exc.reason, kind=exc.kind, hint=text, diagnostics=exc.diagnostics)


def add_connection_guidance(data: dict[str, Any], *, registry_uri: str | None) -> dict[str, Any]:
    """Replace the brief ``<field>_hint`` of each failed status probe with the directive text."""
    for field in STATUS_ERROR_FIELDS:
        kind = data.get(f"{field}_kind")
        text = guidance(kind, registry_uri=registry_uri) if isinstance(kind, str) else None
        if text is not None:
            data[f"{field}_hint"] = text
    return data
