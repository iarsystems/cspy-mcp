"""Command line entry point: ``iar-cspy-mcp`` / ``python -m iar_cspy_mcp``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from iar_cspy import Client, Config
from iar_cspy import cli as cspy_cli

from .server import mcp, set_client


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="iar-cspy-mcp",
        description="MCP server exposing the IAR C-SPY debugger (and IDE services) as tools.",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run MCP server in HTTP mode (streamable-http) on 127.0.0.1 by default.",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help="Port to use with --web (default from MCP_PORT or 8000).",
    )
    parser.add_argument(
        "--probe-cspyserver2",
        action="store_true",
        help="Start the managed backend, print its registry info, then exit.",
    )
    # The backend selection flags are iar_cspy's shared ones (iar_cspy.cli).
    cspy_cli.add_connection_arguments(parser)
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace, environ: dict[str, str] | None = None) -> Config:
    """The backend Config for the command line on top of the environment.

    Raises ``ValueError`` for a flag combination that makes no sense (see
    :func:`iar_cspy.cli.config_from_args`).
    """
    env = dict(os.environ if environ is None else environ)
    if args.iar_path or args.cspyserver2 or args.service_launcher:
        # Naming programs to start means managed mode, whatever the
        # environment says, and stale standalone-mode registry settings must
        # not pin managed startup to an old port. A fixed registry is still
        # possible with --cspyserver2-args "... -registry <port>", and a
        # registry flag given alongside still selects standalone mode.
        env["THRIFT_CSPYSERVER_MODE"] = "managed"
        for name in ("THRIFT_REGISTRY_URI", "THRIFT_REGISTRY_PORT", "THRIFT_REGISTRY_HOST"):
            env.pop(name, None)
    return cspy_cli.config_from_args(args, env)


class _TraceFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        return f"[{ts}] [thrift] {record.getMessage()}"


def _enable_rpc_trace() -> None:
    """Trace every backend RPC to stderr, where MCP hosts collect server logs."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_TraceFormatter())
    logger = logging.getLogger("iar_cspy.rpc")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        config = config_from_args(args)
    except ValueError as exc:
        sys.exit(f"iar-cspy-mcp: error: {exc}")
    client = Client(config, owns_backend=True)
    set_client(client)
    _enable_rpc_trace()

    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if args.web:
        transport = "streamable-http"
        mcp.settings.host = os.getenv("MCP_HOST", "127.0.0.1")
        if args.web_port is not None:
            mcp.settings.port = int(args.web_port)

    if config.mode == "managed":
        host, port = client.backend.start() or ("", 0)
        if args.probe_cspyserver2:
            print(f"Managed CSpyServer2 ready at {host}:{port}")
            print(f"Status: {client.backend.status()}")
            client.close()
            return

    mount_path = os.getenv("MCP_MOUNT_PATH")
    if transport == "stdio":
        print("MCP server ready (stdio). Waiting for an MCP client connection...", file=sys.stderr, flush=True)

    # uvicorn captures SIGTERM, shuts the server down, restores whatever handler
    # was installed beforehand and then re-raises the signal. With the default
    # disposition restored that kills the process outright, so neither the
    # finally below nor the atexit hooks ever run and the backend processes are
    # orphaned. Installing our own handler first means the one uvicorn restores
    # is this one, so the re-raise lands here and the backends get torn down.
    def _terminate(signum: int, _frame: object) -> None:
        client.close()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    try:
        signal.signal(signal.SIGTERM, _terminate)
    except (ValueError, OSError):
        pass  # not the main thread, or unsupported on this platform

    try:
        mcp.run(transport=transport, mount_path=mount_path)
    finally:
        # Covers the exit paths that unwind normally, including Ctrl-C;
        # _terminate above covers SIGTERM. Both are idempotent.
        client.close()


if __name__ == "__main__":
    main()
