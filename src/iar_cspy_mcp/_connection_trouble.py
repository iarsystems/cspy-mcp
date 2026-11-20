"""Turning a dead/unreachable backend into a directive message, not a mystery.

The raw transport text ("Could not connect to ('127.0.0.1', 40557)", "A
service registry ... is required to query services") reads like something to
go investigate - a wrong port, a stale PID file, a process to root-cause.
By the time one of these actually fires, that investigation is not
productive: either no backend has ever been found, or the one that was found
is simply gone, and there is nothing left at that address to look for. This
module turns the raw text into an explicit statement of that fact plus the
one or two legitimate next steps - stop here, don't start pattern-matching
`ps`/`pgrep` output or hunting for coredumps.

cspy-py already classifies failures (``iar_cspy.errors.classify``) into a
stable machine-readable taxonomy; this stays on the mcp-server side of that
boundary; it only adds the natural-language framing an AI caller needs on
top of a code `classify` already produces.
"""

from __future__ import annotations

from typing import Any

from iar_cspy.errors import CSpyError, classify

#: classify()'s codes for "was reachable, now refuses/resets" - a real
#: backend existed and stopped answering, as opposed to never having been found.
_GONE_CODES = {"TRANSPORT_CONNECTION_REFUSED", "TRANSPORT_CONNECTION_RESET"}

#: Substrings of the two distinct "no registry could be resolved, ever" messages:
#: Client.endpoint()'s NotConnectedError and ServiceRegistry._endpoint()'s plain
#: CSpyError (two different exception types, same underlying situation).
_NEVER_FOUND_SNIPPETS = ("service registry", "no registry configured")


def _looks_never_found(text: str) -> bool:
    low = text.lower()
    return any(snippet in low for snippet in _NEVER_FOUND_SNIPPETS)


def connection_hint(exc_or_message: BaseException | str, *, mode: str, registry_uri: str | None) -> str | None:
    """A directive explanation for a dead/unreachable backend, or None to leave it alone.

    ``mode`` is ``client.backend.mode`` and ``registry_uri`` is
    ``client.config.registry_uri``: together they say whether this process owns
    the backend (managed, self-healing), was pointed at a fixed address (ours
    to report, not to restart), or is finding one by auto-discovery (ours to
    wait out, not to search for).
    """
    text = str(exc_or_message)
    if _looks_never_found(text):
        return (
            "No IAR backend is reachable right now, and there has never been "
            "one found to reconnect to. Do not go looking for a process, a "
            "port, or a *.remote discovery file - there is nothing to find. "
            "Start an IAR IDE (or CSpyServer2/IarServiceLauncher) for this "
            "server to discover, or ask the user to, then retry this same call."
        )

    exc = exc_or_message if isinstance(exc_or_message, BaseException) else CSpyError(text)
    if classify(exc).get("code") not in _GONE_CODES:
        return None

    if mode == "managed":
        return (
            "The backend process this server started has exited. That "
            "process is gone - do not search for its old PID or port, and "
            "do not go hunting for a coredump or crash log to explain it; "
            "this server does not control what the IDE does before exiting, "
            "and the explanation would not change what to do next. The next "
            "call restarts it automatically: simply retry. If it keeps "
            "failing, ask the user whether to restart the MCP server itself."
        )
    if registry_uri is not None:
        return (
            f"The configured backend at {registry_uri!r} is no longer "
            "reachable, and this server has no way to restart a process it "
            "does not own. Do not search for its PID or an old port: ask the "
            "user to restart that backend/IDE, or reconfigure this server "
            "(--auto-discovery, or a different registry) to find another one."
        )
    return (
        "The previously-connected IAR IDE process is gone. Do not search for "
        "its PID, its old port, or stale *.remote discovery files - there is "
        "nothing left at that address, and digging for a root cause (a "
        "coredump, a crash log) will not change what to do next; this server "
        "has no visibility into why the IDE exited. Auto-discovery will pick "
        "up a new IDE the next time one registers: give up on this session, "
        "wait, and retry the same call later. If none is expected to start "
        "on its own, ask the user to start or restart their IAR IDE."
    )


#: Status-tool fields known to hold a raw str(exc) from a swallowed exception,
#: rather than raising - connection_hint can't reach these through the normal
#: raised-exception path, so callers add the sibling hint explicitly.
STATUS_ERROR_FIELDS = ("backend_online_error", "core_state_error", "registry_error")


def add_connection_hints(data: dict[str, Any], *, mode: str, registry_uri: str | None) -> dict[str, Any]:
    """Add a ``<field>_hint`` next to any of :data:`STATUS_ERROR_FIELDS` present.

    Never removes or rewrites the original field - the raw diagnostic stays
    available, this only adds the directive reading of it when the text
    matches a known "backend is gone" shape.
    """
    for field in STATUS_ERROR_FIELDS:
        message = data.get(field)
        if isinstance(message, str):
            hint = connection_hint(message, mode=mode, registry_uri=registry_uri)
            if hint:
                data[f"{field}_hint"] = hint
    return data
