import asyncio
import json
import subprocess
import sys

import pytest

pytest.importorskip("mcp.client")
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import (
    CLIENT_CAPABILITIES_META_KEY,
    LATEST_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
)


def command(config):
    return [
        "-m",
        "xyle_trace.mcp",
        "--project",
        config.project,
        "--db",
        str(config.db),
        "--root",
        str(config.root),
        "--contracts",
        str(config.contracts),
    ]


@pytest.mark.parametrize("mode", [LATEST_PROTOCOL_VERSION, "legacy"])
def test_real_stdio_discovery_and_conflicting_updates_from_two_servers(config, mode):
    async def scenario():
        params = StdioServerParameters(
            command=sys.executable, args=command(config), cwd=config.root
        )
        async with (
            Client(params, read_timeout_seconds=10, mode=mode) as first,
            Client(params, read_timeout_seconds=10, mode=mode) as second,
        ):
            assert len((await first.list_tools()).tools) == 8
            initial = await first.call_tool(
                "lineage.register",
                {
                    "request": {
                        "type": "dataset",
                        "natural_key": "inputs",
                        "records": [{"key": "a", "data": {"value": 1}}],
                    }
                },
            )
            row = initial.structured_content["records"][0]

            def edit(value):
                return {
                    "request": {
                        "type": "dataset",
                        "natural_key": "inputs",
                        "records": [
                            {
                                "key": "a",
                                "data": {"value": value},
                                "expected_revision": row["revision"],
                            }
                        ],
                    }
                }

            results = await asyncio.gather(
                first.call_tool("lineage.register", edit(2)),
                second.call_tool("lineage.register", edit(3)),
            )
            assert sum(result.is_error for result in results) == 1
            failure = next(result for result in results if result.is_error)
            assert failure.structured_content["error"]["code"] == "conflict"
            assert json.loads(failure.content[0].text) == failure.structured_content
            winner = next(result for result in results if not result.is_error)
            value = winner.structured_content["records"][0]["record"]["data"]["value"]
            assert (
                await second.call_tool("lineage.register", edit(value))
            ).structured_content == winner.structured_content
            linked = await first.call_tool(
                "lineage.link",
                {
                    "request": {
                        "kind": "edge",
                        "type": "depends_on",
                        "src": initial.structured_content["node"]["node"]["id"],
                        "dst": initial.structured_content["node"]["node"]["id"],
                        "data": {"provisional": True},
                    }
                },
            )
            assert not linked.is_error
            assert linked.structured_content["result"]["edge"]["edge"]["data"]["provisional"]
            assert json.loads(linked.content[0].text) == linked.structured_content

    asyncio.run(scenario())


def test_stdout_contains_only_protocol_messages(config):
    # Feed a real protocol discovery request; the SDK owns parsing and dispatch.
    payload = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                        CLIENT_CAPABILITIES_META_KEY: {},
                    }
                },
            }
        )
        + "\n"
    )
    result = subprocess.run(
        [sys.executable, *command(config)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert messages and all(message["jsonrpc"] == "2.0" for message in messages)
    assert any(message.get("id") == 1 for message in messages)
    assert all("error" not in message for message in messages), messages


@pytest.mark.parametrize("failure", ["arguments", "database", "project", "contracts", "root"])
def test_startup_failure_uses_stderr_without_creating_a_database(config, failure):
    args = command(config)
    if failure == "arguments":
        args = ["-m", "xyle_trace.mcp"]
    else:
        flag = "--db" if failure == "database" else f"--{failure}"
        args[args.index(flag) + 1] = (
            "missing" if failure == "project" else str(config.root / f"missing-{failure}")
        )
    before = set(config.root.iterdir())
    result = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 2 and result.stdout == "" and result.stderr
    assert set(config.root.iterdir()) == before


def test_module_import_has_no_database_or_protocol_side_effects():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3; sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('unexpected DB open')); import xyle_trace.mcp.server",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
