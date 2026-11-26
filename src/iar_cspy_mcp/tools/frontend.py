"""Tools for the headless frontend: what the backend asked, and how to answer.

Without an IDE, iar-cspy stands in for the IDE's ``frontend`` service (message
boxes, file dialogs, source path lookups) and answers on the user's behalf,
declining by default. These tools show those prompts and set answers.
"""

from __future__ import annotations

import json
from typing import Any

from .._app import get_client, mcp


@mcp.tool()
def frontend_get_prompts(clear: bool = False, max_items: int = 50) -> dict[str, Any]:
    """Return what the backend asked the headless frontend, and how it was answered.

    Without an IDE, the server answers the backend's message boxes and dialogs
    itself: message boxes Ok if that is the only button, else Cancel/No;
    file and selection dialogs are cancelled; source path (alias) lookups keep
    the backend's suggestion, else the original name. Each prompt has kind,
    title, message, details, answer and answered_by ("default", "rule" or
    "handler"); notices (kind "message", "edit_source", ...) have no answer.
    A tool that fails lists the prompts of that call in its error, as a
    declined prompt is often the cause; set a different answer with
    frontend_set_answer and retry.

    hosting is false when another frontend (an IDE) answers instead; its
    prompts are not recorded here.
    """
    frontend = get_client().frontend
    prompts = frontend.recent(clear=bool(clear))
    limit = max(0, int(max_items))
    return {
        "hosting": frontend.hosting,
        "count": len(prompts),
        "prompts": [p.to_dict() for p in prompts[-limit:]] if limit else [],
        "progress": [{"id": bar_id, **bar} for bar_id, bar in sorted(frontend.progress.items())],
    }


@mcp.tool()
def frontend_set_answer(pattern: str, answer: str, kind: str = "message_box") -> dict[str, Any]:
    """Answer future backend prompts whose message or title matches pattern.

    pattern is a regular expression, searched in the prompt's message and
    title (see frontend_get_prompts); the most recently set matching answer
    wins. Keep the pattern specific: a broad one (".*", "file") also answers
    unrelated prompts, with side effects such as overwriting files or
    confirming destructive actions. kind is one of message_box, file_dialog, save_dialog,
    directory_dialog, selection, alias, dialog, or "" for any kind.
    answer by kind:
    - message_box: "ok", "cancel", "yes" or "no"
    - file_dialog, save_dialog, directory_dialog: a path, or a JSON array of paths
    - selection: an element's text or index (a JSON array for multiple)
    - alias: the path to use for the file
    - dialog: "ok" or "cancel"
    Answers stay until frontend_clear_answers.
    """
    value = _parse_answer(answer)
    get_client().frontend.answer(pattern, value, kind=kind or None)
    return {"ok": True, "pattern": pattern, "kind": kind or None, "answer": value}


@mcp.tool()
def frontend_clear_answers() -> dict[str, Any]:
    """Forget every answer set with frontend_set_answer; the defaults apply again."""
    get_client().frontend.clear_rules()
    return {"ok": True}


def _parse_answer(answer: str) -> Any:
    """A JSON array or integer as such, anything else as the plain string."""
    text = answer.strip()
    if text.startswith("[") or text.lstrip("-").isdigit():
        try:
            return json.loads(text)
        except ValueError:
            pass
    return answer
