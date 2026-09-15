"""Explicit local execution; never exposed as arbitrary code execution over MCP."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from xyle_trace.contracts.validator import RunBlockedError
from xyle_trace.core.execution import execute
from xyle_trace.service import Service
from xyle_trace.store.sqlite_store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Execute or replay a retained Python/JSON run.")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("execute", "replay"):
        command = commands.add_parser(action)
        for name in ("project", "db", "root", "contracts", "key"):
            command.add_argument(f"--{name}", required=True)
        command.add_argument("--timeout", type=float, default=60)
        if action == "execute":
            command.add_argument("--transformation", required=True)
            command.add_argument("--input", action="append", required=True, dest="inputs")
            command.add_argument("--script", required=True)
            command.add_argument("--params", type=Path, help="JSON object file; defaults to {}")
        else:
            command.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    try:
        options = {"key": args.key, "timeout": args.timeout}
        if args.action == "execute":
            params = json.loads(args.params.read_text()) if args.params else {}
            if not isinstance(params, dict):
                raise ValueError("parameters must be a JSON object")
            options.update(
                transformation_id=args.transformation,
                input_ids=args.inputs,
                script=args.script,
                params=params,
            )
        else:
            options["replay_of"] = args.run
        with closing(Store(Path(args.db), create=False)) as store:
            service = Service(
                store, args.project, root=Path(args.root), contracts_path=Path(args.contracts)
            )
            result = execute(service, **options)
        print(json.dumps(result))
        return 0 if result["data"]["status"] == "succeeded" else 1
    except (OSError, ValueError, sqlite3.Error, RunBlockedError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
