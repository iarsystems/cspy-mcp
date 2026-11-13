"""Command line to backend Config.

Connection flags (--iar-path, --registry-uri, --auto-discovery, ...) are
iar_cspy.cli's, tested there; this only covers the thin wrapper around it
plus this server's own flags (--web, --web-port, --probe-cspyserver2).
"""

from __future__ import annotations

from pathlib import Path

from iar_cspy_mcp.cli import _parse_args, config_from_args


def config(argv, **env):
    return config_from_args(_parse_args(argv), env=env)


def test_defaults_come_from_the_environment(tmp_path):
    cfg = config([], IAR_INSTALL_PATH=str(tmp_path), THRIFT_TIMEOUT_MS="42")
    assert (cfg.mode, cfg.iar_path, cfg.timeout_ms) == ("managed", tmp_path, 42)


def test_connection_flags_reach_iar_cspy_cli(tmp_path):
    cfg = config(["--iar-path", str(tmp_path)])
    assert (cfg.mode, cfg.iar_path) == ("managed", tmp_path)

    cfg = config(["--registry-uri", "thrift://10.0.0.5:5000", "--registry-service", "dbg"])
    assert (cfg.mode, cfg.registry_uri, cfg.registry_service_name) == (
        "standalone",
        "thrift://10.0.0.5:5000",
        "dbg",
    )

    cfg = config(["--auto-discovery", "--workspace", str(tmp_path)])
    assert (cfg.mode, cfg.auto_discovery_enabled, cfg.auto_discovery_workspace) == (
        "standalone",
        True,
        str(tmp_path),
    )


def test_web_flags_are_this_servers_own(tmp_path):
    args = _parse_args(["--web", "--web-port", "9001", "--iar-path", str(tmp_path)])
    assert (args.web, args.web_port) == (True, 9001)
    cfg = config_from_args(args, env={})
    assert cfg.iar_path == Path(tmp_path)


def test_probe_cspyserver2_is_this_servers_own_flag():
    args = _parse_args(["--probe-cspyserver2"])
    assert args.probe_cspyserver2 is True
