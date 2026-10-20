#!/usr/bin/env bash
# Run the bridge over HTTP against a Thrift-enabled IarIde (the IAR IDE).
#
#   ./run_iaride.sh <iar-path> [--workspace <file.eww>] [--registry-file <path>]
#
# <iar-path> is an IAR installation or build stage, i.e. the directory with
# under it. iaride is taken from <iar-path>/common/bin.
#
# Env vars:
#   IAR_INSTALL_PATH  the installation, if not given as an argument
#   IARIDE_EXE        override the iaride path
#   THRIFT_REGISTRY_FILE  override the service registry file path
#   MCP_PORT          HTTP port to listen on (default 8000)
#
# IarIde hosts the IDE platform services - ProjectManager and OptionsService -
# so the project_* and options_* tools work against it. If you do not have an
# IDE to hand, run_headless.sh gets the same services from IarServiceLauncher
# without a GUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALLER_DIR="$(pwd -P)"

PYTHON="$SCRIPT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

WEB_PORT="${MCP_PORT:-8000}"
ENV_IAR_INSTALL_PATH="${IAR_INSTALL_PATH:-}"
IAR_INSTALL_PATH=""
WORKSPACE_PATH=""
REGISTRY_FILE="${THRIFT_REGISTRY_FILE:-}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace)
      if [ "$#" -lt 2 ]; then
        echo "--workspace requires a path to an .eww file." >&2
        exit 2
      fi
      WORKSPACE_PATH="$2"
      shift 2
      ;;
    --registry-file)
      if [ "$#" -lt 2 ]; then
        echo "--registry-file requires a path." >&2
        exit 2
      fi
      REGISTRY_FILE="$2"
      shift 2
      ;;
    --iar-path)
      if [ "$#" -lt 2 ]; then
        echo "--iar-path requires a path." >&2
        exit 2
      fi
      IAR_INSTALL_PATH="$2"
      shift 2
      ;;
    --*)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
    *)
      if [ -n "$IAR_INSTALL_PATH" ]; then
        echo "Unexpected argument: $1" >&2
        exit 2
      fi
      IAR_INSTALL_PATH="$1"
      shift
      ;;
  esac
done

if [ -z "$IAR_INSTALL_PATH" ]; then
  IAR_INSTALL_PATH="$ENV_IAR_INSTALL_PATH"
fi
if [ -n "$IAR_INSTALL_PATH" ] && [[ "$IAR_INSTALL_PATH" != /* ]]; then
  IAR_INSTALL_PATH="$CALLER_DIR/$IAR_INSTALL_PATH"
fi

if [ -z "$IAR_INSTALL_PATH" ] && [ -z "${IARIDE_EXE:-}" ]; then
  echo "Path to an IAR installation is required." >&2
  echo "Pass it as the first argument or with --iar-path, or set IAR_INSTALL_PATH." >&2
  echo "It is the directory with common/bin under it." >&2
  exit 1
fi
IARIDE="${IARIDE_EXE:-$IAR_INSTALL_PATH/common/bin/iaride}"
if [[ "$IARIDE" != /* ]]; then
  IARIDE="$CALLER_DIR/$IARIDE"
fi
if [ ! -x "$IARIDE" ]; then
  echo "iaride not found or not executable: $IARIDE" >&2
  exit 1
fi

if [ -n "$WORKSPACE_PATH" ]; then
  if [[ "$WORKSPACE_PATH" != /* ]]; then
    WORKSPACE_PATH="$CALLER_DIR/$WORKSPACE_PATH"
  fi
  WORKSPACE_DIR="$(cd "$(dirname "$WORKSPACE_PATH")" && pwd -P)" || {
    echo "Workspace directory not found: $(dirname "$WORKSPACE_PATH")" >&2
    exit 1
  }
  WORKSPACE_PATH="$WORKSPACE_DIR/$(basename "$WORKSPACE_PATH")"
  if [ ! -f "$WORKSPACE_PATH" ]; then
    echo "Workspace file not found: $WORKSPACE_PATH" >&2
    exit 1
  fi
  PROJECT_DIR="$WORKSPACE_DIR"
else
  PROJECT_DIR="$CALLER_DIR"
fi

if [ -z "$REGISTRY_FILE" ]; then
  REGISTRY_FILE="$PROJECT_DIR/RemoteControlServiceLocation.json"
elif [[ "$REGISTRY_FILE" != /* ]]; then
  REGISTRY_FILE="$CALLER_DIR/$REGISTRY_FILE"
fi

echo "Starting IarIde in $PROJECT_DIR${WORKSPACE_PATH:+ with $WORKSPACE_PATH} ..." >&2
cd "$PROJECT_DIR"
IARIDE_ARGS=()
if [ -n "$WORKSPACE_PATH" ]; then
  IARIDE_ARGS+=("$WORKSPACE_PATH")
fi
"$IARIDE" "${IARIDE_ARGS[@]}" &
IARIDE_PID=$!
trap 'kill "$IARIDE_PID" 2>/dev/null || true' EXIT

echo "Using IarIde service registry file: $REGISTRY_FILE" >&2
echo "MCP endpoint: http://127.0.0.1:$WEB_PORT/mcp" >&2

# Keep the MCP package importable while running from the workspace directory.
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Not exec'd (unlike run_web.sh): we need to stay alive after the server exits
# so the EXIT trap above can shut IarIde down too.
"$PYTHON" -m mcp_thrift_server \
  --web \
  --web-port "$WEB_PORT" \
  --registry-file "$REGISTRY_FILE"
