"""A portable fixture replayed only through real stdio tools and the init CLI."""

import asyncio
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp.client")
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import LATEST_PROTOCOL_VERSION

FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "minimal"


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


async def call(client, tool, request, *, error=None, actions=None):
    if actions is not None:
        actions.append(tool)
    response = await client.call_tool(f"lineage.{tool}", {"request": request})
    result = response.structured_content
    assert json.loads(response.content[0].text) == result
    if error:
        assert response.is_error and result["error"]["code"] == error, result
    else:
        assert not response.is_error, result
    return result["result"] if tool == "link" and not error else result


def binding(row):
    return {
        "dataset_id": row["record"]["dataset_id"],
        "record_key": row["record"]["key"],
        "expected_record_revision": row["revision"],
        "expected_resolution_revision": row["resolution_revision"],
    }


def anchor(explanation):
    return next(n for n in explanation["nodes"] if n["node"]["id"] == explanation["node_id"])


def observations(path):
    with path.open(newline="") as stream:
        return {
            row["sample"]: {
                "sample": row["sample"],
                "value": int(row["value"]),
                "unit": row["unit"],
            }
            for row in csv.DictReader(stream)
        }


async def explain(client, node_id):
    return await call(client, "query", {"mode": "explain", "node_id": node_id})


async def review(client, claim_id, key, rationale):
    state = anchor(await explain(client, claim_id))
    request = {
        "kind": "review",
        "natural_key": key,
        "rationale": rationale,
        "claims": [
            {
                "claim_id": claim_id,
                "expected_revision": state["revision"],
                "review_status": "reviewed",
            }
        ],
    }
    result = await call(client, "decide", request)
    assert (await call(client, "decide", request)) == result
    assert anchor(await explain(client, claim_id))["node"]["data"]["review_status"] == "reviewed"
    return request


async def reported_sum(client, root, dataset_id, transformation_id, key):
    start_request = {
        "action": "start",
        "execution_key": key,
        "transformation_id": transformation_id,
        "input_ids": [dataset_id],
        "params": {"operation": "sum"},
        "code_reference": None,
    }
    started = await call(client, "record_run", start_request)
    assert await call(client, "record_run", start_request) == started
    assert not started["reproducible"] and started["capture_mode"] == "reported"
    assert {
        "execution_not_observed",
        "environment_unknown",
        "code_reference_unknown",
        "immutable_input_contents_not_retained",
    } <= set(started["capture_gaps"])

    # Computation is performed by this client, not by the MCP server.
    rows = (await explain(client, dataset_id))["records"]
    total = sum(row["record"]["data"]["value"] for row in rows)
    output_path = root / f"{key}.json"
    output_path.write_text(json.dumps({"total": total, "unit": "count"}) + "\n")
    output = await call(
        client,
        "register",
        {
            "type": "artifact",
            "natural_key": key + "-output",
            "data": {"path": output_path.name},
        },
    )
    output_id = output["node"]["node"]["id"]
    finish_request = {
        "action": "finish",
        "run_id": started["run"]["node"]["id"],
        "expected_revision": started["run"]["revision"],
        "status": "succeeded",
        "outputs": [{"node_id": output_id, "path": output_path.name}],
    }
    finished = await call(client, "record_run", finish_request)
    assert finished["output_file_hashes"][output_id] == digest(output_path.read_bytes())
    assert finished["input_fingerprints"] == started["input_fingerprints"]
    assert finished["policy_hash"] == digest((root / "contracts.yaml").read_bytes())
    assert finished["run"]["node"]["data"]["status"] == "succeeded"
    assert not finished["reproducible"] and finished["capture_mode"] == "reported"
    assert await call(client, "record_run", finish_request) == finished
    assert await call(client, "record_run", start_request) == finished
    return total, output_id, start_request, finish_request


async def prepare_interrupted_workflow(client, root):
    assert {tool.name for tool in (await client.list_tools()).tools} == {
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
    source_request = {
        "type": "source",
        "natural_key": "survey",
        "data": {"uri": "source-v1.csv", "title": "Sample counts"},
    }
    source = await call(client, "register", source_request)
    assert await call(client, "register", source_request) == source
    source_id = source["node"]["node"]["id"]
    capture_request = {"kind": "local", "source_id": source_id, "path": "source-v1.csv"}
    snapshot = await call(client, "snapshot", capture_request)
    retried = await call(client, "snapshot", capture_request)
    assert retried["already_existed"] and retried["snapshot"] == snapshot["snapshot"]
    assert snapshot["content_hash"] == digest((root / "source-v1.csv").read_bytes())
    assert (root / snapshot["path"]).read_bytes() == (root / "source-v1.csv").read_bytes()
    records = json.loads((root / "records.json").read_text())
    observed = observations(root / snapshot["path"])
    assert all(record["data"] == observed[record["key"]] for record in records)
    dataset_request = {"type": "dataset", "natural_key": "observations", "records": records}
    dataset = await call(client, "register", dataset_request)
    dataset_id = dataset["node"]["node"]["id"]
    assert await call(client, "register", dataset_request) == dataset

    actions = []
    gaps = await call(client, "validate", {"run_name": "analysis.sum"}, actions=actions)
    assert len(gaps["gaps"]) == 2 and gaps["run_blocked"] and not gaps["valid"]
    evidence_request = {
        "kind": "evidence",
        "natural_key": "survey-v1-rows",
        "data": {
            "snapshot_id": snapshot["snapshot"]["node"]["id"],
            "locator": {"kind": "range", "value": "CSV lines 2–3 (A and B)"},
            "provisional": False,
        },
        "records": [binding(gap["record"]) for gap in gaps["gaps"]],
    }
    evidence = await call(client, "link", evidence_request, actions=actions)
    valid = await call(client, "validate", {"run_name": "analysis.sum"}, actions=actions)
    assert valid["valid"] and not valid["run_blocked"] and valid["gaps"] == []
    assert actions == ["validate", "link", "validate"]
    assert len(evidence["records"]) == 2 and evidence["violations"] == []
    assert await call(client, "link", evidence_request) == evidence

    transformation = await call(
        client,
        "register",
        {
            "type": "transformation",
            "natural_key": "analysis.sum",
            "data": {"description": "Sum observed counts"},
        },
    )
    transformation_id = transformation["node"]["node"]["id"]
    total, output_id, start_request, _ = await reported_sum(
        client, root, dataset_id, transformation_id, "sum-v1"
    )
    assert total == 30
    claim_request = {
        "type": "claim",
        "natural_key": "current-total",
        "data": {"text": "The current sample total is 30 count.", "status": "supported"},
    }
    claim = await call(client, "register", claim_request)
    claim_id = claim["node"]["node"]["id"]
    support_request = {
        "kind": "edge",
        "type": "supports",
        "src": output_id,
        "dst": claim_id,
        "data": {"provisional": False, "rationale": "Sum of the two observed counts."},
    }
    await call(client, "link", support_request)
    old_review = await review(client, claim_id, "review-v1", "Checked 10 + 20 = 30 against v1.")
    explanation = await explain(client, claim_id)
    assert explanation["source_ids"] == [source_id]
    assert {
        dataset_id,
        output_id,
        evidence["node"]["node"]["id"],
        snapshot["snapshot"]["node"]["id"],
    } <= {node["node"]["id"] for node in explanation["nodes"]}
    evidence_state = next(n for n in explanation["nodes"] if n["node"]["type"] == "evidence_link")
    assert evidence_state["node"]["data"]["locator"] == evidence_request["data"]["locator"]
    exported = await call(client, "export", {})
    before_retries = exported["graph"]
    await call(client, "register", claim_request)
    await call(client, "link", support_request)
    await call(client, "link", evidence_request)
    await call(client, "register", dataset_request)
    assert (await call(client, "export", {}))["graph"] == before_retries

    # A new release changes A. Save only the actual tool receipt for local evidence;
    # the next client receives no Python object, database handle, or remembered IDs.
    updated = await call(
        client,
        "snapshot",
        {
            "kind": "local",
            "source_id": source_id,
            "path": "source-v2.csv",
        },
    )
    assert updated["snapshot"]["node"]["id"] != snapshot["snapshot"]["node"]["id"]
    (root / "capture-receipt.json").write_text(json.dumps(updated))
    changed = observations(root / updated["path"])["A"]
    row = next(
        row for row in (await explain(client, dataset_id))["records"] if row["record"]["key"] == "A"
    )
    correction_request = {
        "kind": "correction",
        "natural_key": "adopt-survey-v2",
        "rationale": "Release v2 revises sample A from 10 to 12 count.",
        "changes": [
            {
                "dataset_id": dataset_id,
                "key": "A",
                "data": changed,
                "expected_revision": row["revision"],
            }
        ],
    }
    corrected = await call(client, "decide", correction_request)
    assert corrected["records"][0]["resolution"] is None
    assert corrected["records"][0]["revision"] != row["revision"]
    correction_retry = await call(client, "decide", correction_request)
    assert correction_retry["node"] == corrected["node"]
    assert correction_retry["records"] == corrected["records"]
    assert correction_retry["claims"] == []  # No newly invalidated claims on an exact retry.
    blocked = await call(client, "validate", {"run_name": "analysis.sum"})
    assert not blocked["valid"] and blocked["run_blocked"] and len(blocked["gaps"]) == 1
    assert blocked["gaps"][0]["record"]["record"]["data"] == changed
    await call(
        client, "record_run", {**start_request, "execution_key": "blocked-run"}, error="run_blocked"
    )
    await call(client, "link", evidence_request, error="conflict")
    await call(client, "register", dataset_request, error="conflict")
    await call(client, "decide", old_review, error="conflict")
    await call(client, "register", claim_request)
    assert (
        anchor(await explain(client, claim_id))["node"]["data"]["review_status"] == "needs_review"
    )
    graph = (await call(client, "export", {}))["graph"]
    assert not any(node["natural_key"] == "blocked-run" for node in graph["nodes"])
    (root / "initial-actions.json").write_text(json.dumps(actions))


async def recover_from_work_items(client, root):
    # This function deliberately has no access to the first session's responses.
    # Capture receipt is a persisted, unmodified snapshot tool result.
    persisted = (await call(client, "export", {}))["graph"]
    pending = [node for node in persisted["nodes"] if node["type"] == "claim"]
    assert len(pending) == 1 and pending[0]["data"]["review_status"] == "needs_review"
    # The export above checks persistence before any mutation; resolution below
    # derives its IDs and revisions solely from validation and the capture receipt.
    receipt = json.loads((root / "capture-receipt.json").read_text())
    captured_path = root / receipt["path"]
    assert digest(captured_path.read_bytes()) == receipt["content_hash"]
    observed = observations(captured_path)
    actions = []
    work = await call(client, "validate", {}, actions=actions)
    assert not work["valid"] and work["blocked_patterns"] == ["analysis.*"]
    assert len(work["gaps"]) == 1
    gap = work["gaps"][0]
    row = gap["record"]
    assert gap["violation"]["kind"] == "unresolved_record"
    assert gap["requirements"][0]["evidence"] == "exact_locator"
    assert "evidence_backed" in gap["permitted_statuses"]
    assert row["record"]["data"] == observed[row["record"]["key"]]
    assert row["resolution"] is None and row["resolution_revision"] is None
    recovery_request = {
        "kind": "evidence",
        "natural_key": "survey-v2-corrected-row",
        "data": {
            "snapshot_id": receipt["snapshot"]["node"]["id"],
            "provisional": False,
            "locator": {"kind": "range", "value": "CSV line 2 (A)"},
        },
        "records": [binding(row)],
    }
    resolved = await call(client, "link", recovery_request, actions=actions)
    valid = await call(client, "validate", {}, actions=actions)
    assert valid["valid"] and valid["gaps"] == [] and valid["blocked_patterns"] == []
    assert actions == ["validate", "link", "validate"]
    assert resolved["violations"] == []
    retry = await call(client, "link", recovery_request)
    assert retry["node"] == resolved["node"] and retry["records"] == resolved["records"]
    assert retry["claims"] == [] and retry["violations"] == []
    dataset_id = gap["dataset_id"]
    impact = await call(client, "query", {"mode": "impact", "node_id": dataset_id})
    assert len(impact["claims_needing_review"]) == 1
    claim_id = impact["claims_needing_review"][0]
    claim_state = anchor(await explain(client, claim_id))
    assert claim_state["node"]["data"]["review_status"] == "needs_review"

    explanation = await explain(client, claim_id)
    transformation_id = next(
        n["node"]["id"] for n in explanation["nodes"] if n["node"]["type"] == "transformation"
    )
    old_output_id = next(
        n["node"]["id"] for n in explanation["nodes"] if n["node"]["type"] == "artifact"
    )
    total, output_id, _, _ = await reported_sum(
        client, root, dataset_id, transformation_id, "sum-v2"
    )
    assert total == 32
    claim_request = {
        "type": "claim",
        "natural_key": claim_state["node"]["natural_key"],
        "expected_revision": anchor(await explain(client, claim_id))["revision"],
        "data": {"text": "The current sample total is 32 count.", "status": "supported"},
    }
    await call(client, "register", claim_request)
    # The old result remains historical, and its support for the current claim
    # is explicitly withdrawn without deleting the run or its bytes.
    old_support = next(edge for edge in explanation["edges"] if edge["edge"]["type"] == "supports")
    await call(
        client,
        "link",
        {
            "kind": "edge",
            "type": "supports",
            "src": old_output_id,
            "dst": claim_id,
            "expected_revision": old_support["revision"],
            "data": {"provisional": True, "rationale": "Historical total; superseded by v2."},
        },
    )
    new_support = {
        "kind": "edge",
        "type": "supports",
        "src": output_id,
        "dst": claim_id,
        "data": {"provisional": False, "rationale": "Recomputed 12 + 20 = 32 from backed records."},
    }
    await call(client, "link", new_support)
    await review(
        client, claim_id, "review-v2", "Checked corrected A, unchanged B, and recomputed total 32."
    )
    before = await call(client, "export", {})
    await call(client, "link", recovery_request)
    await call(client, "register", claim_request)
    await call(client, "link", new_support)
    after = await call(client, "export", {})
    assert after == before
    assert anchor(await explain(client, claim_id))["node"]["data"]["review_status"] == "reviewed"
    graph = after["graph"]
    assert len(graph["nodes"]) == len({node["id"] for node in graph["nodes"]})
    assert len(graph["edges"]) == len({edge["id"] for edge in graph["edges"]})
    assert len(graph["datasets"][dataset_id]["records"]) == 2
    assert len(graph["datasets"][dataset_id]["resolutions"]) == 2
    runs = [node for node in graph["nodes"] if node["type"] == "run"]
    assert len(runs) == 2 and all(not run["data"]["reproducible"] for run in runs)
    snapshots = [node for node in graph["nodes"] if node["type"] == "source_snapshot"]
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert (
            digest((root / snapshot["data"]["path"]).read_bytes())
            == snapshot["data"]["content_hash"]
        )
    assert json.loads((root / "sum-v1.json").read_text())["total"] == 30
    written = await call(client, "export", {"destination": "lineage.json"})
    assert written["graph"] is None and written["content_hash"] == after["content_hash"]
    assert json.loads((root / written["path"]).read_text()) == graph
    assert digest((root / written["path"]).read_bytes()) == written["content_hash"]
    assert await call(client, "export", {"destination": "lineage.json"}) == written
    html = await call(client, "export", {"format": "html", "destination": "lineage.html"})
    assert html["graph"] is None
    assert digest((root / html["path"]).read_bytes()) == html["content_hash"]
    assert "Lineage explorer" in (root / html["path"]).read_text()
    initial_actions = json.loads((root / "initial-actions.json").read_text())
    print(
        f"Resolution calls: initial evidence batch={len(initial_actions)}, recovery={len(actions)}"
    )


@pytest.fixture
def minimal_project(tmp_path):
    for name in ("source-v1.csv", "source-v2.csv", "records.json", "contracts.yaml"):
        shutil.copyfile(FIXTURE / name, tmp_path / name)
    database = tmp_path / ".lineage" / "graph.db"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "xyle_trace.cli.main",
            "init",
            "--project",
            "minimal",
            "--db",
            str(database),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return StdioServerParameters(
        command=sys.executable,
        cwd=tmp_path,
        args=[
            "-m",
            "xyle_trace.mcp",
            "--project",
            "minimal",
            "--db",
            str(database),
            "--root",
            str(tmp_path),
            "--contracts",
            str(tmp_path / "contracts.yaml"),
        ],
    )


@pytest.mark.parametrize("mode", [LATEST_PROTOCOL_VERSION, "legacy"])
def test_workflow_continues_after_server_restart(tmp_path, minimal_project, mode):
    params = minimal_project

    async def first_session():
        async with Client(params, read_timeout_seconds=15, mode=mode) as client:
            await prepare_interrupted_workflow(client, tmp_path)

    asyncio.run(first_session())  # Client and server close before any recovery.

    async def fresh_session():
        async with Client(params, read_timeout_seconds=15, mode=mode) as client:
            await recover_from_work_items(client, tmp_path)

    asyncio.run(fresh_session())


def test_assumption_batch_resolves_in_three_calls(tmp_path, minimal_project):
    async def scenario():
        async with Client(minimal_project, read_timeout_seconds=15) as client:
            # A separate hypothetical scenario: these values are explicit assumptions,
            # not transcribed observations. No source capture is claimed here.
            await call(
                client,
                "register",
                {
                    "type": "dataset",
                    "natural_key": "observations",
                    "data": {"description": "Hypothetical baseline for a scenario"},
                    "records": json.loads((tmp_path / "records.json").read_text()),
                },
            )
            actions = []
            gaps = await call(client, "validate", {}, actions=actions)
            assert len(gaps["gaps"]) == 2
            assert all("assumption_backed" in gap["permitted_statuses"] for gap in gaps["gaps"])
            request = {
                "kind": "assumption",
                "natural_key": "scenario-baseline",
                "assumption_type": "scenario",
                "provisional": False,
                "rationale": "Deliberately assume A=10 and B=20 count for this hypothetical baseline.",
                "records": [binding(gap["record"]) for gap in gaps["gaps"]],
            }
            decision = await call(client, "decide", request, actions=actions)
            valid = await call(client, "validate", {}, actions=actions)
            assert valid["valid"] and valid["gaps"] == []
            assert valid["assumptions"]["assumptions"] == 2 and valid["assumptions"]["rate"] == 1
            assert actions == ["validate", "decide", "validate"]
            before = await call(client, "export", {})
            assert await call(client, "decide", request) == decision
            assert await call(client, "export", {}) == before
            print(f"Resolution calls: assumption batch={len(actions)}")

    asyncio.run(scenario())
