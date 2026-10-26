import pytest

from xyle_trace.models.entities import Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


def test_correction_retains_prior_value_backing_and_rationale_and_is_immutable(tmp_path):
    store = Store(tmp_path / "graph.db")
    try:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
        service = Service(store, "p", contracts=[], root=tmp_path)
        rows = service.register(
            {
                "type": "dataset",
                "natural_key": "inputs",
                "records": [{"key": "a", "data": {"value": 7}}],
            }
        )
        backing = service.decide(
            {
                "kind": "assumption",
                "natural_key": "proxy",
                "assumption_type": "proxy",
                "rationale": "Original assumption",
                "provisional": False,
                "records": [
                    {
                        "dataset_id": rows.node.node.id,
                        "record_key": "a",
                        "expected_record_revision": rows.records[0].revision,
                    }
                ],
            }
        )
        request = {
            "kind": "correction",
            "natural_key": "correction",
            "rationale": "New observation",
            "changes": [
                {
                    "dataset_id": rows.node.node.id,
                    "key": "a",
                    "data": {"value": 8},
                    "expected_revision": rows.records[0].revision,
                }
            ],
        }
        result = service.decide(request)
        before = result.node.node.data["previous_records"][0]
        assert before["record"]["data"]["value"] == 7
        assert before["resolution"]["target_id"] == backing.node.node.id
        assert before["backing"]["node"]["data"]["rationale"] == "Original assumption"
        assert service.store.resolutions("p", rows.node.node.id) == {}
        assert service.decide(request).node == result.node
        with pytest.raises(ServiceError) as error:
            service.decide(
                {
                    **request,
                    "rationale": "Rewrite the past",
                    "expected_revision": result.node.revision,
                }
            )
        assert error.value.error.code == "conflict"
        assert service.store.get_node("p", result.node.node.id).data == result.node.node.data
    finally:
        store.close()


def test_methodology_and_correction_decisions_reach_their_affected_results(tmp_path):
    store = Store(tmp_path / "graph.db")
    try:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
        service = Service(store, "p", contracts=[], root=tmp_path)
        rows = service.register({"type": "dataset", "natural_key": "inputs"})
        claim = service.register(
            {"type": "claim", "natural_key": "result", "data": {"text": "The result"}}
        )
        service.link(
            {
                "kind": "edge",
                "type": "supports",
                "src": rows.node.node.id,
                "dst": claim.node.node.id,
                "data": {"provisional": False},
            }
        )
        method = service.decide(
            {
                "kind": "methodology",
                "natural_key": "method",
                "rationale": "Equal weights",
                "subject_ids": [rows.node.node.id],
            }
        )
        result = service.query({"mode": "impact", "node_id": method.node.node.id})
        assert result.claims_needing_review == [claim.node.node.id]
        explained = service.query({"mode": "explain", "node_id": claim.node.node.id})
        assert method.node.node.id in {state.node.id for state in explained.nodes}
    finally:
        store.close()


def test_natural_key_correction_is_visible_without_rewriting_its_receipt(tmp_path):
    store = Store(tmp_path / "graph.db")
    try:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
        service = Service(store, "p", contracts=[], root=tmp_path)
        rows = service.register(
            {"type": "dataset", "natural_key": "inputs",
             "records": [{"key": "a", "data": {"value": 7}}]}
        )
        claim = service.register(
            {"type": "claim", "natural_key": "result", "data": {"text": "The result"}}
        ).node.node
        service.link({"kind": "edge", "type": "supports", "src": rows.node.node.id,
                      "dst": claim.id, "data": {"provisional": False}})
        request = {
            "kind": "correction", "natural_key": "correction", "rationale": "New observation",
            "changes": [{"dataset_id": "inputs", "key": "a", "data": {"value": 8},
                         "expected_revision": rows.records[0].revision}],
        }
        correction = service.decide(request)
        explanation = service.query({"mode": "explain", "node_id": claim.id})
        assert correction.node.node.id in {item["id"]
                                          for item in explanation.explanation["decisions"]}
        assert claim.id in service.query(
            {"mode": "impact", "node_id": correction.node.node.id}
        ).claims_needing_review
        assert correction.node.node.data["changes"][0]["dataset_id"] == "inputs"
        assert service.decide(request).node == correction.node
        report = service.export({"format": "report", "selected_ids": [claim.id]}).graph
        reported = next(item["data"] for item in report["nodes"]
                        if item["id"] == correction.node.node.id)
        assert reported["changes"] == correction.node.node.data["changes"]
        assert "omitted_unselected_changes" not in reported
    finally:
        store.close()
