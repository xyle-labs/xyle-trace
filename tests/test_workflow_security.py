"""Synthetic integration regressions for safe sharing and historical computation."""

import json
from contextlib import closing

import pytest

from xyle_trace.core.execution import execute
from xyle_trace.core.handoff import export_project, import_project
from xyle_trace.demo import run_demo
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.service import Service
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / ".lineage" / "graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    yield Service(store, "p", root=tmp_path, contracts=[])
    store.close()


def sum_run(service, dataset_id, key):
    path = service.root / "sum.py"
    path.write_text(
        "import json,sys\n"
        "payload=json.load(open(sys.argv[1]))\n"
        'total=sum(row["record"]["data"]["value"] for inp in payload["inputs"].values() '
        'for row in inp["records"])\n'
        'json.dump({"total":total},open(sys.argv[2],"w"))\n'
    )
    transformation = service.register({"type": "transformation", "natural_key": "sum", "data": {}})
    run = execute(
        service,
        key=key,
        transformation_id=transformation.node.node.id,
        input_ids=[dataset_id],
        script="sum.py",
    )
    artifact = next(
        node
        for node in service.store.nodes_of_type("p", NodeType.ARTIFACT)
        if run["id"] in node.natural_key
    )
    return run, artifact


def test_chained_computation_keeps_the_selected_derived_dataset_rows(service):
    original = service.register(
        {
            "type": "dataset",
            "natural_key": "original",
            "records": [{"key": "a", "data": {"value": 7}}],
        }
    )
    _, artifact = sum_run(service, original.node.node.id, "first")
    derived = service.register(
        {
            "type": "dataset",
            "natural_key": "derived",
            "records": [{"key": "derived-row", "data": {"value": 14}}],
        }
    )
    service.link(
        {
            "kind": "edge",
            "type": "derived_from",
            "src": derived.node.node.id,
            "dst": artifact.id,
            "data": {"provisional": False},
        }
    )
    result, _ = sum_run(service, derived.node.node.id, "second")
    assert result["data"]["status"] == "succeeded"
    actual = json.loads((service.root / result["data"]["output"]["path"]).read_text())
    assert actual["total"] == 14


def test_invalid_retained_context_does_not_echo_blob_contents_in_error_details(service):
    from xyle_trace.core.snapshots import SnapshotCapture

    rows = service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [{"key": "a", "data": {"value": 7}}],
        }
    )
    run, artifact = sum_run(service, rows.node.node.id, "original")
    bundle = json.loads((service.root / run["data"]["bundle"]["path"]).read_bytes())
    bundle["input"]["inputs"][rows.node.node.id]["records"] = ["PRIVATE_BLOB_CONTENT_SENTINEL"]
    retained = SnapshotCapture(service.root).bytes(json.dumps(bundle).encode())
    current = service.store.get_node("p", run["id"])
    current.data["bundle"] = {"path": retained.path, "content_hash": retained.content_hash}
    service.store.upsert_node(current)
    result = service.query({"mode": "explain", "node_id": artifact.id})
    assert "PRIVATE_BLOB_CONTENT_SENTINEL" not in result.model_dump_json()
    assert any(gap["kind"] == "retained_inputs_unavailable" for gap in result.explanation["gaps"])


def test_selected_report_does_not_disclose_an_unrelated_correction(service):
    shared = service.register(
        {
            "type": "dataset",
            "natural_key": "public-input",
            "records": [{"key": "a", "data": {"value": 1}}],
        }
    )
    private = service.register(
        {
            "type": "dataset",
            "natural_key": "private-input",
            "records": [{"key": "private-row", "data": {"value": 2}}],
        }
    )
    result = service.register(
        {
            "type": "indicator",
            "natural_key": "public-result",
            "data": {"definition": "Public result"},
        }
    )
    service.link(
        {
            "kind": "edge",
            "type": "derived_from",
            "src": result.node.node.id,
            "dst": shared.node.node.id,
            "data": {"provisional": False},
        }
    )
    service.decide(
        {
            "kind": "correction",
            "natural_key": "batch-correction",
            "rationale": "Corrected observations",
            "changes": [
                {
                    "dataset_id": shared.node.node.id,
                    "key": "a",
                    "data": {"value": 3},
                    "expected_revision": shared.records[0].revision,
                },
                {
                    "dataset_id": private.node.node.id,
                    "key": "private-row",
                    "data": {"value": "CONFIDENTIAL_UNSELECTED_OBSERVATION"},
                    "expected_revision": private.records[0].revision,
                },
            ],
        }
    )
    service.export(
        {"format": "report", "selected_ids": [result.node.node.id], "destination": "selected.html"}
    )
    html = (service.root / "selected.html").read_text()
    assert "CONFIDENTIAL_UNSELECTED_OBSERVATION" not in html
    assert private.node.node.id not in html


def test_managed_demo_handoff_preserves_claim_review_and_resolution_state(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    with closing(Store(root / ".lineage" / "graph.db")) as store:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    demo = run_demo(root, "p")
    with closing(Store(root / ".lineage" / "graph.db", create=False)) as store:
        service = Service(store, "p", root=root, contracts_path=root / "contracts.yaml")
        claim = service.register(
            {
                "type": "claim",
                "natural_key": "original-total",
                "data": {"text": "Original total was 30", "status": "supported"},
            }
        )
        artifact = next(
            node
            for node in store.nodes_of_type("p", NodeType.ARTIFACT)
            if demo["original"]["id"] in node.natural_key
        )
        service.link(
            {
                "kind": "edge",
                "type": "supports",
                "src": artifact.id,
                "dst": claim.node.node.id,
                "data": {"provisional": False},
            }
        )
        current = service.query({"mode": "explain", "node_id": claim.node.node.id})
        claim_state = next(state for state in current.nodes if state.node.id == claim.node.node.id)
        service.decide(
            {
                "kind": "review",
                "natural_key": "verified-original",
                "rationale": "Original retained evidence checked",
                "claims": [
                    {
                        "claim_id": claim.node.node.id,
                        "expected_revision": claim_state.revision,
                        "review_status": "reviewed",
                    }
                ],
            }
        )
        before = export_graph(store, "p")
        export_project(
            store,
            "p",
            root,
            root / "project.zip",
            include_content=True,
            contracts_path=root / "contracts.yaml",
        )
    restored = tmp_path / "restored"
    import_project(root / "project.zip", restored)
    with closing(Store(restored / ".lineage" / "graph.db", create=False)) as store:
        assert export_graph(store, "p") == before
        service = Service(store, "p", root=restored, contracts_path=restored / "contracts.yaml")
        original = service.query({"mode": "explain", "node_id": artifact.id, "selector": "/total"})
        assert original.explanation["selection"]["value"] == 30
        assert store.get_node("p", claim.node.node.id).data["review_status"] == "reviewed"
        assert not (restored / "sum.py").exists()
        default = service.export({"format": "bundle", "destination": "metadata.zip"})
        assert default.path == "metadata.zip"


def test_multi_result_report_keeps_each_historical_dataset_version(service):
    rows = service.register(
        {
            "type": "dataset",
            "natural_key": "observations",
            "data": {"description": "DESCRIPTION_AT_FIRST_RUN"},
            "records": [{"key": "a", "data": {"value": 7}}],
        }
    )
    _, first = sum_run(service, rows.node.node.id, "first")
    service.register(
        {
            "type": "dataset",
            "natural_key": "observations",
            "expected_revision": rows.node.revision,
            "data": {"description": "DESCRIPTION_AT_SECOND_RUN"},
        }
    )
    _, second = sum_run(service, rows.node.node.id, "second")
    service.export(
        {"format": "report", "selected_ids": [first.id, second.id], "destination": "versions.html"}
    )
    html = (service.root / "versions.html").read_text()
    first_section, second_section = html.split("<article ")[1:]
    assert "DESCRIPTION_AT_FIRST_RUN" in first_section
    assert "DESCRIPTION_AT_SECOND_RUN" not in first_section
    assert "DESCRIPTION_AT_SECOND_RUN" in second_section
    assert "DESCRIPTION_AT_FIRST_RUN" not in second_section


def test_default_explanations_and_reports_omit_retained_source_and_program_bytes(service):
    source_path = service.root / "raw.txt"
    source_path.write_text("PRIVATE_SOURCE_BYTES_SENTINEL")
    source = service.register(
        {"type": "source", "natural_key": "input", "data": {"uri": "raw.txt"}}
    )
    snapshot = service.snapshot(
        {"kind": "local", "source_id": source.node.node.id, "path": "raw.txt"}
    )
    transformation = service.register(
        {"type": "transformation", "natural_key": "constant", "data": {}}
    )
    (service.root / "constant.py").write_text(
        "# PRIVATE_EXECUTABLE_SOURCE_SENTINEL\n"
        'import json,sys\njson.dump({"count":1},open(sys.argv[2],"w"))\n'
    )
    run = execute(
        service,
        key="capture",
        transformation_id=transformation.node.node.id,
        input_ids=[snapshot.snapshot.node.id],
        script="constant.py",
    )
    artifact = next(
        node
        for node in service.store.nodes_of_type("p", NodeType.ARTIFACT)
        if run["id"] in node.natural_key
    )
    explained = service.query({"mode": "explain", "node_id": artifact.id})
    query_json = explained.model_dump_json()
    service.export({"format": "report", "selected_ids": [artifact.id], "destination": "safe.html"})
    report = (service.root / "safe.html").read_text()
    for secret in ("PRIVATE_SOURCE_BYTES_SENTINEL", "PRIVATE_EXECUTABLE_SOURCE_SENTINEL"):
        assert secret not in query_json
        assert secret not in report
    assert "file_base64" not in query_json
    assert explained.explanation["selection"]["value"] == {"count": 1}
