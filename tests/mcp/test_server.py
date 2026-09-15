import asyncio
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

pytest.importorskip("mcp.client")
from mcp import Client

from xyle_trace.core.snapshots import CaptureLimits
from xyle_trace.mcp.server import create_server
from xyle_trace.store.sqlite_store import Store


def test_discovery_and_typed_success(config):
    async def scenario():
        async with Client(create_server(config)) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                f"lineage.{name}"
                for name in (
                    "register",
                    "snapshot",
                    "record_run",
                    "link",
                    "decide",
                    "query",
                    "validate",
                    "export",
                )
            }
            for tool in tools:
                assert tool.input_schema["additionalProperties"] is False
                assert tool.input_schema["required"] == ["request"]
                assert tool.output_schema
                assert tool.annotations.read_only_hint == (
                    tool.name in {"lineage.query", "lineage.validate"}
                )
            assert next(
                t for t in tools if t.name == "lineage.snapshot"
            ).annotations.open_world_hint
            for name in ("register", "link", "decide", "export"):
                assert next(
                    t for t in tools if t.name == f"lineage.{name}"
                ).annotations.destructive_hint
            result = await client.call_tool(
                "lineage.register",
                {
                    "request": {
                        "type": "dataset",
                        "natural_key": "inputs",
                        "records": [{"key": "a", "data": {"value": True}}],
                    }
                },
            )
            assert not result.is_error
            assert json.loads(result.content[0].text) == result.structured_content
            assert result.structured_content["records"][0]["record"]["data"]["value"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"request": {}, "project": "other"},
        {"request": {"project": "other"}},
        {"request": "{}"},
        {"request": {"dataset": 1}},
    ],
)
def test_raw_arguments_are_strict_and_return_structured_errors(config, arguments):
    async def scenario():
        async with Client(create_server(config)) as client:
            result = await client.call_tool("lineage.validate", arguments)
            assert result.is_error
            assert result.structured_content["error"]["code"] == "invalid_request"
            assert json.loads(result.content[0].text) == result.structured_content

    asyncio.run(scenario())


def test_business_errors_and_validation_gaps_are_distinct(config):
    server = create_server(config)
    config.contracts.write_text("- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n")

    async def scenario():
        async with Client(server) as client:
            missing = await client.call_tool(
                "lineage.query", {"request": {"mode": "explain", "node_id": "missing"}}
            )
            assert missing.is_error and missing.structured_content["error"]["code"] == "not_found"
            gaps = await client.call_tool("lineage.validate", {"request": {}})
            assert not gaps.is_error and gaps.structured_content["valid"] is False
            config.contracts.write_text("broken: [")
            failed = await client.call_tool("lineage.validate", {"request": {}})
            assert (
                failed.is_error and failed.structured_content["error"]["code"] == "contract_error"
            )

    asyncio.run(scenario())


def test_each_handler_opens_and_closes_store_in_its_worker(config, monkeypatch):
    from xyle_trace.mcp import server as module

    server = create_server(config)
    events = []
    main_thread = threading.get_ident()

    class TrackedStore(Store):
        def __init__(self, *args, **kwargs):
            self.thread = threading.get_ident()
            events.append(("open", self.thread))
            super().__init__(*args, **kwargs)

        def close(self):
            assert self.thread == threading.get_ident()
            events.append(("close", self.thread))
            super().close()

    monkeypatch.setattr(module, "Store", TrackedStore)

    async def scenario():
        async with Client(server) as client:
            await client.call_tool("lineage.validate", {"request": {}})
            await client.call_tool(
                "lineage.query", {"request": {"mode": "explain", "node_id": "missing"}}
            )

    asyncio.run(scenario())
    assert [event for event, _ in events] == ["open", "close", "open", "close"]
    assert all(thread != main_thread for _, thread in events)


def test_policy_is_read_once_per_validation_and_deleted_db_is_not_recreated(config, monkeypatch):
    server = create_server(config)
    read = Path.read_bytes
    calls = []

    def tracked(path):
        if path == config.contracts:
            calls.append(path)
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", tracked)

    async def scenario():
        async with Client(server) as client:
            assert not (await client.call_tool("lineage.validate", {"request": {}})).is_error
            assert calls == [config.contracts]
            config.db.unlink()
            assert (await client.call_tool("lineage.validate", {"request": {}})).is_error
            assert not config.db.exists()

    asyncio.run(scenario())


def test_configuration_is_immutable_and_rejects_missing_project_or_unrecognized_db(config):
    with pytest.raises(FrozenInstanceError):
        config.project = "other"
    with pytest.raises(ValueError):
        create_server(replace(config, project="other"))
    blank = config.root / "blank.db"
    blank.touch()
    with pytest.raises(ValueError):
        create_server(replace(config, db=blank))
    assert blank.read_bytes() == b""
    with sqlite3.connect(blank) as connection:
        connection.execute("PRAGMA user_version=99")
    before = blank.read_bytes()
    with pytest.raises(ValueError):
        create_server(replace(config, db=blank))
    assert blank.read_bytes() == before


def test_remaining_tools_return_schema_valid_structured_results(config):
    async def scenario():
        async with Client(create_server(config)) as client:

            async def call(name, request):
                result = await client.call_tool(f"lineage.{name}", {"request": request})
                assert not result.is_error, result.content
                assert json.loads(result.content[0].text) == result.structured_content
                return result.structured_content

            source = await call(
                "register",
                {"type": "source", "natural_key": "source", "data": {"uri": "input.txt"}},
            )
            (config.root / "input.txt").write_text("observed")
            snapshot = await call(
                "snapshot",
                {"kind": "local", "source_id": source["node"]["node"]["id"], "path": "input.txt"},
            )
            await call(
                "link",
                {
                    "kind": "evidence",
                    "natural_key": "evidence",
                    "data": {
                        "snapshot_id": snapshot["snapshot"]["node"]["id"],
                        "provisional": False,
                        "locator": {"kind": "section", "value": "All"},
                    },
                },
            )
            await call(
                "decide",
                {
                    "kind": "assumption",
                    "natural_key": "proxy",
                    "assumption_type": "proxy",
                    "rationale": "Documented estimate",
                    "provisional": True,
                },
            )
            transformation = await call(
                "register", {"type": "transformation", "natural_key": "compute", "data": {}}
            )
            started = await call(
                "record_run",
                {
                    "action": "start",
                    "execution_key": "one",
                    "transformation_id": transformation["node"]["node"]["id"],
                    "input_ids": [snapshot["snapshot"]["node"]["id"]],
                    "code_reference": None,
                },
            )
            assert started["reproducible"] is False
            await call(
                "record_run",
                {
                    "action": "finish",
                    "run_id": started["run"]["node"]["id"],
                    "expected_revision": started["run"]["revision"],
                    "status": "failed",
                    "failure_detail": "Reported failure",
                },
            )
            await call("query", {"mode": "explain", "node_id": started["run"]["node"]["id"]})
            assert (await call("export", {}))["node_count"] == 7

    asyncio.run(scenario())


def test_startup_transfer_limits_are_applied_to_snapshot_capture(config):
    config = replace(config, capture_limits=CaptureLimits(max_bytes=2))
    (config.root / "input.txt").write_bytes(b"too large")

    async def scenario():
        async with Client(create_server(config)) as client:
            source = await client.call_tool(
                "lineage.register",
                {
                    "request": {
                        "type": "source",
                        "natural_key": "source",
                        "data": {"uri": "input.txt"},
                    }
                },
            )
            result = await client.call_tool(
                "lineage.snapshot",
                {
                    "request": {
                        "kind": "local",
                        "source_id": source.structured_content["node"]["node"]["id"],
                        "path": "input.txt",
                    }
                },
            )
            assert (
                result.is_error and result.structured_content["error"]["code"] == "capture_failed"
            )

    asyncio.run(scenario())
