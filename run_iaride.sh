#!/usr/bin/env bash
# Run the MCP server over HTTP against a Thrift-enabled IarIde (the IAR IDE).
#
#   ./run_iaride.sh <iar-path>
#
# <iar-path> is an IAR installation or build stage, i.e. the directory with
# common/bin under it. iaride is taken from <iar-path>/common/bin.
#
# Env vars:
#   IAR_INSTALL_PATH  the installation, if not given as an argument
#   IARIDE_EXE  override the iaride path
#   MCP_PORT    HTTP port to listen on (default 8000)
#
# IarIde hosts the IDE platform services - ProjectManager and OptionsService -
# so the project_* and options_* tools work against it. If you do not have an
# IDE to hand, run_headless.sh gets the same services from IarServiceLauncher
# without a GUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Run the server from the checkout without installing it. Its dependencies,
# including iar-cspy (https://github.com/iarsystems/cspy-py), must be installed,
# e.g. into .venv with `pip install -r requirements.txt`.
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

WEB_PORT="${MCP_PORT:-8000}"
IAR_INSTALL_PATH="${1:-${IAR_INSTALL_PATH:-}}"

if [ -z "$IAR_INSTALL_PATH" ] && [ -z "${IARIDE_EXE:-}" ]; then
  echo "Path to an IAR installation is required." >&2
  echo "Pass it as the first argument or set IAR_INSTALL_PATH." >&2
  echo "It is the directory with common/bin under it." >&2
  exit 1
fi
IARIDE="${IARIDE_EXE:-$IAR_INSTALL_PATH/common/bin/iaride}"
if [ ! -x "$IARIDE" ]; then
  echo "iaride not found or not executable: $IARIDE" >&2
  exit 1
fi

# Unlike run_web.sh (which lets iar_cspy_mcp spawn and manage CSpyServer2
# itself), IarIde is a long-lived GUI app, so we start it here and let
# iar_cspy_mcp --auto-discovery find its registry through the *.remote
# discovery file IarIde writes (default ~/.iar/auto_discovery): no waiting or
# parsing here, iar_cspy retries discovery on every call until it appears. If
# another IDE session is also running, narrow the search with --workspace
# <file.eww> or --auto-discovery-dir.
"$IARIDE" &
IARIDE_PID=$!
trap 'kill "$IARIDE_PID" 2>/dev/null || true' EXIT

echo "Starting IarIde (pid $IARIDE_PID)" >&2
echo "MCP endpoint: http://127.0.0.1:$WEB_PORT/mcp" >&2

# Not exec'd (unlike run_web.sh): we need to stay alive after the server exits
# so the EXIT trap above can shut IarIde down too.
"$PYTHON" -m iar_cspy_mcp \
  --web \
  --web-port "$WEB_PORT" \
  --auto-discovery
