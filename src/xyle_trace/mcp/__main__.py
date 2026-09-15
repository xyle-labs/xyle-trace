"""Run the optional adapter over stdio; diagnostics never use protocol stdout."""

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

from xyle_trace.core.snapshots import CaptureLimits

_FIELDS = ("project", "db", "root", "contracts")


class DiscoveryError(ValueError):
    """Project settings could not be determined from flags, env, or the working directory."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve one initialized lineage project over MCP stdio."
    )
    parser.add_argument("--project")
    for name in ("db", "root", "contracts"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--capture-timeout", type=float, default=30)
    parser.add_argument("--capture-max-bytes", type=int, default=25 * 1024 * 1024)
    parser.add_argument("--capture-max-redirects", type=int, default=5)
    return parser


def _discover(cwd: Path, env: Mapping[str, str]) -> dict[str, str | None]:
    """Read project settings for ``cwd`` from ``.lineage/config.json`` and the
    conventional layout, with ``XYLE_TRACE_*`` environment overrides.

    The project id has no convention (unlike the db, root, and contracts paths):
    a bare working directory does not name its own project, so it must come from
    ``config.json`` or the environment when no ``--project`` flag is given.
    """
    config_path = cwd / ".lineage" / "config.json"
    config: dict = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise DiscoveryError(f"failed to read {config_path}: {error}") from error
        if not isinstance(config, dict):
            raise DiscoveryError(f"{config_path} must contain a JSON object")

    defaults = {
        "db": cwd / ".lineage" / "graph.db",
        "root": cwd,
        "contracts": cwd / "contracts.yaml",
    }
    resolved: dict[str, str | None] = {}
    for key in _FIELDS:
        resolved[key] = env.get(f"XYLE_TRACE_{key.upper()}") or config.get(key) or defaults.get(key)
    return resolved


def _resolve(
    args: argparse.Namespace, cwd: Path, env: Mapping[str, str]
) -> tuple[str, Path, Path, Path]:
    """Explicit flags win field-by-field; anything absent falls back to discovery.

    All four flags given reproduces the previous required-flags behavior exactly.
    """
    if all(getattr(args, name) for name in _FIELDS):
        return args.project, args.db, args.root, args.contracts

    discovered = _discover(cwd, env)
    project = args.project or discovered["project"]
    db = args.db or discovered["db"]
    root = args.root or discovered["root"]
    contracts = args.contracts or discovered["contracts"]
    if not project:
        raise DiscoveryError(
            "no project configured: pass --project, set XYLE_TRACE_PROJECT, or add "
            '"project" to ./.lineage/config.json (no MCP server was started)'
        )
    return project, Path(db), Path(root), Path(contracts)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        from xyle_trace.mcp.server import ServerConfig, create_server
    except ModuleNotFoundError as error:
        if error.name == "mcp":
            print(
                "MCP support is optional. Install with: pip install 'xyle-trace[mcp]'",
                file=sys.stderr,
            )
            return 2
        raise
    try:
        project, db, root, contracts = _resolve(args, Path.cwd(), os.environ)
    except DiscoveryError as error:
        print(f"MCP configuration failed: {error}", file=sys.stderr)
        return 2
    try:
        limits = CaptureLimits(
            args.capture_timeout, args.capture_max_bytes, args.capture_max_redirects
        )
        server = create_server(ServerConfig(project, db, root, contracts, limits))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"MCP configuration failed: {error}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
