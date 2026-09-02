"""Minimal end-to-end demo against a live C-SPY backend.

Uses the bundled simulator launch configuration in tests/live_assets.

Prerequisites:
- Point the server at CSpyServer2, e.g.:
    set THRIFT_CSPYSERVER_EXE=C:\\iar\\<toolchain>\\common\\bin\\CSpyServer2.exe
  (managed mode, default) or export THRIFT_REGISTRY_HOST/PORT for external mode.

Run from the repository root:
    python examples/run_live_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a plain script from anywhere: put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_thrift_server.server import (  # noqa: E402
    breakpoints_get_all,
    breakpoints_set_on_ule,
    debugger_configure_and_start_session,
    debugger_get_modules,
    debugger_go_and_wait_for_core_state,
    debugger_session_status,
    debugger_stop_session,
)


def load_launch_json() -> str:
    launch_file = Path(__file__).resolve().parents[1] / "tests" / "live_assets" / "launch.json"
    raw = json.loads(launch_file.read_text(encoding="utf-8"))
    cfg = raw["configurations"][0]
    for key in ("program", "projectPath"):
        value = cfg.get(key)
        if value and not Path(value).is_absolute():
            cfg[key] = str((launch_file.parent / value).resolve())
    return json.dumps(cfg)


def main() -> int:
    launch_json = load_launch_json()

    print("STEP configure_and_start")
    out = debugger_configure_and_start_session(launch_json)
    print(json.dumps(out, indent=2, default=str))
    if not out.get("ok"):
        return 1

    print("STEP session_status")
    print(json.dumps(debugger_session_status(), indent=2, default=str))

    print("STEP modules")
    print(debugger_get_modules())

    print("STEP breakpoint on main")
    print(breakpoints_set_on_ule("main", 1))
    print(breakpoints_get_all())

    print("STEP go and wait for halt")
    print(json.dumps(debugger_go_and_wait_for_core_state(desired_state=0, timeout_ms=10000), indent=2, default=str))

    print("STEP stop_session")
    try:
        print(json.dumps(debugger_stop_session(), indent=2, default=str))
    except Exception as exc:  # noqa: BLE001
        # stopSession can hit a known backend assertion in standalone mode;
        # the session itself already ran, so don't fail the demo on teardown.
        print(f"stop_session failed (known backend teardown issue): {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
