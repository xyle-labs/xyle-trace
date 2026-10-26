import json

import pytest

from xyle_trace.core.execution import execute
from xyle_trace.core.revisions import revision
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / ".lineage" / "graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    yield Service(store, "p", contracts=[], root=tmp_path)
    store.close()


def managed(service):
    rows = service.register(
        {
            "type": "dataset",
            "natural_key": "observations",
            "records": [{"key": "a", "data": {"value": 7}}],
        }
    )
    transformation = service.register(
        {"type": "transformation", "natural_key": "sum", "data": {"code_reference": "sum.py"}}
    )
    (service.root / "sum.py").write_text(
        "import json,sys\n"
        "d=json.load(open(sys.argv[1]))\n"
        'v=sum(r["record"]["data"]["value"] for x in d["inputs"].values() '
        'for r in x["records"])\n'
        'json.dump({"total":v},open(sys.argv[2],"w"))\n'
    )
    run = execute(
        service,
        key="original",
        transformation_id=transformation.node.node.id,
        input_ids=[rows.node.node.id],
        script="sum.py",
        params={"factor": 1},
    )
    artifact = next(n for n in service.store.nodes_of_type("p", NodeType.ARTIFACT))
    return rows, run, artifact


def test_historical_explanation_uses_retained_rows_after_correction(service):
    rows, run, artifact = managed(service)
    correction = service.decide(
        {
            "kind": "correction",
            "natural_key": "fix",
            "rationale": "Corrected observation",
            "changes": [
                {
                    "dataset_id": rows.node.node.id,
                    "key": "a",
                    "data": {"value": 99},
                    "expected_revision": rows.records[0].revision,
                }
            ],
        }
    )
    result = service.query(
        {"mode": "explain", "node_id": artifact.id, "run_id": run["id"], "selector": "/total"}
    )
    explanation = result.explanation
    assert explanation["selection"]["value"] == 7
    assert explanation["selection"]["historical"] is True
    assert explanation["inputs"][0]["records"][0]["record"]["data"]["value"] == 7
    assert explanation["inputs"][0]["basis"] == "retained"
    assert explanation["execution"]["state"] == "executed"
    assert explanation["calculations"][0]["parameters"] == {"factor": 1}
    assert "code" not in explanation["calculations"][0]
    # Current corrections are context, never silently merged into historical inputs.
    current = service.query({"mode": "explain", "node_id": rows.node.node.id}).explanation
    assert correction.node.node.id in {d["id"] for d in current["decisions"]}
    assert any(g["kind"] == "current_inputs_differ" for g in explanation["gaps"])


def test_replay_and_explicit_code_inspection_are_distinct(service):
    _, run, _ = managed(service)
    replay = execute(service, key="replay", replay_of=run["id"])
    artifact = next(
        n
        for n in service.store.nodes_of_type("p", NodeType.ARTIFACT)
        if replay["id"] in n.natural_key
    )
    result = service.query(
        {"mode": "explain", "node_id": artifact.id, "include_code": True}
    ).explanation
    assert result["execution"]["state"] == "replayed"
    assert result["execution"]["reproducible"] is True
    assert "json.dump" in result["calculations"][0]["code"]


def test_methodology_is_visible_and_version_pin_cannot_silently_advance(service):
    rows = service.register({"type": "dataset", "natural_key": "inputs"})
    decision = service.decide(
        {
            "kind": "methodology",
            "natural_key": "method",
            "rationale": "Equal weights chosen",
            "subject_ids": [rows.node.node.id],
        }
    )
    result = service.query(
        {"mode": "explain", "node_id": rows.node.node.id, "revision": rows.node.revision}
    ).explanation
    assert result["decisions"][0]["id"] == decision.node.node.id
    with pytest.raises(ServiceError) as error:
        service.query(
            {
                "mode": "explain",
                "node_id": rows.node.node.id,
                "revision": revision({"different": True}),
            }
        )
    assert error.value.error.code == "conflict"


def test_changed_reported_output_is_not_used_for_a_recorded_field(service):
    rows = service.register({"type": "dataset", "natural_key": "inputs"})
    transformation = service.register(
        {"type": "transformation", "natural_key": "external", "data": {}}
    )
    (service.root / "result.json").write_text(json.dumps({"total": 7}))
    artifact = service.register(
        {"type": "artifact", "natural_key": "result", "data": {"path": "result.json"}}
    )
    run = service.record_run(
        {
            "action": "start",
            "execution_key": "external-1",
            "transformation_id": transformation.node.node.id,
            "input_ids": [rows.node.node.id],
            "code_reference": None,
        }
    )
    service.record_run(
        {
            "action": "finish",
            "run_id": run.run.node.id,
            "expected_revision": run.run.revision,
            "status": "succeeded",
            "outputs": [{"node_id": artifact.node.node.id}],
        }
    )
    (service.root / "result.json").write_text(json.dumps({"total": 999}))
    result = service.query(
        {"mode": "explain", "node_id": artifact.node.node.id, "selector": "/total"}
    ).explanation
    assert result["selection"]["value"] is None
    assert result["execution"]["state"] == "reported"
    assert result["execution"]["reproducible"] is False
    assert any(g["kind"] == "output_hash_mismatch" for g in result["gaps"])


def test_unrelated_run_and_invalid_pointer_are_rejected(service):
    _, run, _ = managed(service)
    other = service.register({"type": "artifact", "natural_key": "other", "data": {}})
    with pytest.raises(ServiceError) as error:
        service.query({"mode": "explain", "node_id": other.node.node.id, "run_id": run["id"]})
    assert error.value.error.code == "invalid_reference"
    with pytest.raises(ServiceError) as error:
        service.query({"mode": "explain", "node_id": other.node.node.id, "selector": "total"})
    assert error.value.error.code == "invalid_request"


def test_managed_claim_keeps_its_current_support_path_to_retained_inputs(service):
    rows, run, artifact = managed(service)
    indicator = service.register(
        {"type": "indicator", "natural_key": "total", "data": {"definition": "Total"}}
    ).node.node
    claim = service.register(
        {"type": "claim", "natural_key": "finding",
         "data": {"text": "The total is seven", "status": "analytical_inference"}}
    ).node.node
    connectors = []
    for kind, source, target in (
        ("derived_from", indicator, artifact), ("supports", indicator, claim)
    ):
        connectors.append(service.link(
            {"kind": "edge", "type": kind, "src": source.id, "dst": target.id,
             "data": {"provisional": False}}
        ).edge.edge.id)
    service.decide(
        {"kind": "correction", "natural_key": "later", "rationale": "Later observation",
         "changes": [{"dataset_id": rows.node.node.id, "key": "a", "data": {"value": 99},
                      "expected_revision": rows.records[0].revision}]}
    )

    result = service.query({"mode": "explain", "node_id": claim.id})
    assert {claim.id, indicator.id, artifact.id, run["id"], rows.node.node.id} <= {
        state.node.id for state in result.nodes
    }
    assert set(connectors) <= {state.edge.id for state in result.edges}
    assert any(state.edge.type == "produced" and state.edge.dst == artifact.id
               for state in result.edges)
    assert result.explanation["inputs"][0]["records"][0]["record"]["data"] == {"value": 7}
    assert any(gap["kind"] == "current_selection_context"
               for gap in result.explanation["gaps"])
    service.export({"format": "report", "selected_ids": [claim.id], "destination": "claim.html"})
    report = (service.root / "claim.html").read_text()
    assert "No upstream connection recorded" not in report
    assert artifact.natural_key in report


def test_managed_explanation_marks_unretained_transformation_metadata(service):
    _, run, artifact = managed(service)
    transform_id = run["data"]["execution_request"]["transformation_id"]
    transform = service.store.get_node("p", transform_id)
    transform.data["description"] = "A later replacement method"
    service.store.upsert_node(transform)
    result = service.query({"mode": "explain", "node_id": artifact.id})
    assert transform_id not in {state.node.id for state in result.nodes}
    assert any(gap["kind"] == "transformation_metadata_unretained"
               for gap in result.explanation["gaps"])
    assert result.explanation["calculations"][0]["transformation_id"] == transform_id


def test_managed_claim_discloses_current_conflicting_and_provisional_branches(service):
    _, _, artifact = managed(service)
    claim = service.register(
        {"type": "claim", "natural_key": "disputed",
         "data": {"text": "The total is seven", "status": "conflicting_sources"}}
    ).node.node
    service.link({"kind": "edge", "type": "supports", "src": artifact.id, "dst": claim.id,
                  "data": {"provisional": False}})
    branches = []
    for kind, provisional in (("contradicts", False), ("supports", True)):
        dataset = service.register(
            {"type": "dataset", "natural_key": kind,
             "records": [{"key": "later", "data": {"value": 99}}]}
        ).node.node
        edge = service.link(
            {"kind": "edge", "type": kind, "src": dataset.id, "dst": claim.id,
             "data": {"provisional": provisional}}
        ).edge.edge
        branches.append(edge)
    result = service.query({"mode": "explain", "node_id": claim.id})
    gap = next(g for g in result.explanation["gaps"]
               if g["kind"] == "current_result_relationships_omitted")
    assert set(gap["edge_ids"]) == {edge.id for edge in branches}
    assert set(gap["relationship_types"]) == {"supports", "contradicts"}
    assert gap["provisional_edge_ids"] == [branches[1].id]
    assert not {edge.src for edge in branches}.intersection(
        item["node_id"] for item in result.explanation["inputs"]
    )
