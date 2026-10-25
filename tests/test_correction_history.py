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
