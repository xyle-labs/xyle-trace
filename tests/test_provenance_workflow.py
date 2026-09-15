"""The core boundary the MCP layer must preserve, using a generic evidence chain."""

import json

import pytest

from xyle_trace.cli.main import main
from xyle_trace.contracts.loader import load_contracts
from xyle_trace.contracts.validator import RunBlockedError, run_guarded, validate_contracts
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.queries.traversal import explain, impact, upstream
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def workflow(tmp_path):
    store = Store(tmp_path / "graph.db")

    def node(kind, key, **data):
        return store.upsert_node(Node(project_id="p", type=kind, natural_key=key, data=data))

    source = node(NodeType.SOURCE, "survey")
    snapshot = node(
        NodeType.SOURCE_SNAPSHOT,
        "survey-v1",
        source_id=source.id,
        content_hash=hash_bytes(b"observations: 10"),
    )
    evidence = node(
        NodeType.EVIDENCE_LINK,
        "observation",
        snapshot_id=snapshot.id,
        locator={"kind": "table", "value": "Table 1, row A"},
    )
    dataset = node(NodeType.DATASET, "inputs")
    record = Record(project_id="p", dataset_id=dataset.id, key="A", data={"value": 10})
    store.put_records([record])
    resolution = Resolution(
        project_id="p",
        dataset_id=dataset.id,
        record_key="A",
        status="evidence_backed",
        target_id=evidence.id,
    )
    contracts_file = tmp_path / "contracts.yaml"
    contracts_file.write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed, assumption_backed]\n"
        "    evidence: exact_locator\n  blocks: [analysis.*]\n"
    )
    contracts = load_contracts(contracts_file)
    yield store, source, snapshot, evidence, dataset, record, resolution, contracts, contracts_file
    store.close()


def test_registration_resolution_computation_correction_and_recovery(workflow, capsys):
    store, source, snapshot, evidence, dataset, record, resolution, contracts, path = workflow
    calls = []
    operation = lambda: calls.append("executed") or 20
    with pytest.raises(RunBlockedError) as exc:
        run_guarded(store, "p", "analysis.double", contracts, operation)
    assert exc.value.violations[0].kind == "unresolved_record"
    assert calls == []

    store.put_resolution(resolution)
    assert validate_contracts(store, "p", contracts) == []
    assert run_guarded(store, "p", "analysis.double", contracts, operation) == 20
    assert calls == ["executed"]
    run = store.upsert_node(Node(project_id="p", type=NodeType.RUN, natural_key="analysis.double"))
    artifact = store.upsert_node(Node(project_id="p", type=NodeType.ARTIFACT, natural_key="table"))
    claim = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.CLAIM,
            natural_key="result",
            data={"status": "supported", "review_status": "reviewed"},
        )
    )
    for kind, src, dst in [
        (EdgeType.USED_INPUT, run.id, dataset.id),
        (EdgeType.PRODUCED, run.id, artifact.id),
        (EdgeType.SUPPORTS, artifact.id, claim.id),
    ]:
        store.upsert_edge(Edge(project_id="p", type=kind, src=src, dst=dst))

    explained = explain(store, "p", claim.id)
    assert explained["sources"] == [source.id]
    assert {dataset.id, evidence.id, snapshot.id, run.id, artifact.id} <= set(explained["upstream"])
    assert next(n for n in explained["nodes"] if n["id"] == evidence.id)["data"]["locator"] == {
        "kind": "table",
        "value": "Table 1, row A",
    }
    assert upstream(store, "p", dataset.id, max_depth=1) == [evidence.id]
    assert upstream(store, "other", dataset.id) == []
    assert impact(store, "p", source.id)["claims_needing_review"] == [claim.id]
    assert store.get_node("p", claim.id).data["review_status"] == "reviewed"  # query is read-only

    # Retries preserve evidence, timestamps, and claim review state.
    store.put_records([record])
    first = store.put_resolution(resolution)
    assert store.put_resolution(resolution).created_at == first.created_at
    assert store.get_node("p", claim.id).data["review_status"] == "reviewed"
    assert (
        main(["validate", "--project", "p", "--db", str(store.path), "--contracts", str(path)]) == 0
    )
    capsys.readouterr()
    assert main(["explain", "--project", "p", "--db", str(store.path), "--node", claim.id]) == 0
    assert json.loads(capsys.readouterr().out)["sources"] == [source.id]

    store.put_records([record.model_copy(update={"data": {"value": 999}})])
    assert store.resolutions("p", dataset.id) == {}
    assert store.unresolved_keys("p", dataset.id) == ["A"]
    assert store.get_node("p", claim.id).data == {
        "status": "supported",
        "review_status": "needs_review",
    }
    assert impact(store, "p", dataset.id)["claims_needing_review"] == [claim.id]
    with pytest.raises(RunBlockedError):
        run_guarded(store, "p", "analysis.double", contracts, operation)
    assert calls == ["executed"]
    assert (
        main(["validate", "--project", "p", "--db", str(store.path), "--contracts", str(path)]) == 1
    )
    assert "unresolved_record" in capsys.readouterr().out

    # A fresh session can discover the gap and record a documented assumption.
    fresh = Store(store.path)
    try:
        gap = validate_contracts(fresh, "p", contracts)[0]
        decision = fresh.upsert_node(
            Node(
                project_id="p",
                type=NodeType.DECISION,
                natural_key="proxy",
                data={"assumption_type": "proxy", "rationale": "Explicit scenario input."},
            )
        )
        fresh.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=gap.dataset,
                record_key=gap.record_key,
                status="assumption_backed",
                target_id=decision.id,
            )
        )
        assert run_guarded(fresh, "p", "analysis.double", contracts, operation) == 20
        exported = export_graph(fresh, "p")
        assert exported["datasets"][dataset.id]["resolutions"]["A"]["target_id"] == decision.id
        assert fresh.get_node("p", claim.id).data["review_status"] == "needs_review"
        assert fresh.get_node("p", snapshot.id) == snapshot
    finally:
        fresh.close()


def test_evidence_correction_invalidates_resolutions_and_flags_claims(workflow):
    store, _, _, evidence, dataset, _, resolution, contracts, _ = workflow
    store.put_resolution(resolution)
    claim = store.upsert_node(Node(project_id="p", type=NodeType.CLAIM, natural_key="claim"))
    store.upsert_edge(Edge(project_id="p", type=EdgeType.SUPPORTS, src=dataset.id, dst=claim.id))
    evidence.data["provisional"] = True
    store.upsert_node(evidence)
    assert store.resolutions("p", dataset.id) == {}
    assert store.get_node("p", claim.id).data["review_status"] == "needs_review"
    assert validate_contracts(store, "p", contracts)[0].kind == "unresolved_record"


def test_snapshot_versions_cannot_be_overwritten(workflow):
    store, _, snapshot, *_ = workflow
    changed = snapshot.model_copy(
        update={"data": {**snapshot.data, "content_hash": hash_bytes(b"new")}}
    )
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_node(changed)
    assert store.get_node("p", snapshot.id) == snapshot
    assert store.upsert_node(snapshot) == snapshot


def test_validation_and_traversal_normalize_references_consistently(workflow):
    store, source, _, evidence, dataset, _, resolution, contracts, _ = workflow
    evidence.data["snapshot_id"] = " " + evidence.data["snapshot_id"] + " "
    store.upsert_node(evidence)
    store.put_resolution(resolution)
    assert validate_contracts(store, "p", contracts) == []
    assert explain(store, "p", dataset.id)["sources"] == [source.id]
