"""New capabilities remain modes/fields on the existing eight MCP tools."""

import asyncio
import json
import zipfile

import pytest

pytest.importorskip("mcp.client")
from mcp import Client

from xyle_trace.mcp.server import create_server


def test_health_explanation_and_selected_sharing_use_the_existing_protocol(config):
    async def scenario():
        async with Client(create_server(config)) as client:
            registered = await client.call_tool(
                "lineage.register",
                {
                    "request": {
                        "type": "indicator",
                        "natural_key": "Coverage",
                        "data": {
                            "definition": "Synthetic coverage",
                            "unit": "%",
                            "metadata": {"value": 42},
                        },
                    }
                },
            )
            assert not registered.is_error
            node = registered.structured_content["node"]
            selected = node["node"]["id"]
            result = await client.call_tool(
                "lineage.query",
                {
                    "request": {
                        "mode": "explain",
                        "node_id": selected,
                        "revision": node["revision"],
                    }
                },
            )
            assert not result.is_error
            assert result.structured_content["explanation"]["selection"]["value"] == 42
            checked = await client.call_tool("lineage.validate", {"request": {"health": True}})
            assert not checked.is_error
            assert checked.structured_content["health"]["policy"]["valid"] is True
            preview = await client.call_tool(
                "lineage.export",
                {
                    "request": {
                        "format": "report",
                        "selected_ids": [selected],
                    }
                },
            )
            assert not preview.is_error
            assert preview.structured_content["path"] is None
            report = await client.call_tool(
                "lineage.export",
                {
                    "request": {
                        "format": "report",
                        "selected_ids": [selected],
                        "destination": "report.html",
                    }
                },
            )
            assert not report.is_error
            assert (
                preview.structured_content["content_hash"]
                == report.structured_content["content_hash"]
            )
            assert (config.root / "report.html").is_file()
            bundled = await client.call_tool(
                "lineage.export",
                {
                    "request": {
                        "format": "bundle",
                        "destination": "project.zip",
                    }
                },
            )
            assert not bundled.is_error
            with zipfile.ZipFile(config.root / "project.zip") as archive:
                assert json.loads(archive.read("manifest.json"))["include_content"] is False
            assert len((await client.list_tools()).tools) == 8

    asyncio.run(scenario())
