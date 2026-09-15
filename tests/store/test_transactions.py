from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from xyle_trace.core.revisions import (
    RevisionConflictError,
    check_revision,
    require_revision,
    revision,
)
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "graph.db")
    dataset = store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="inputs"))
    record = Record(project_id="p", dataset_id=dataset.id, key="r", data={"value": 1})
    store.put_records([record])
    decision = store.upsert_node(Node(project_id="p", type=NodeType.DECISION, natural_key="basis"))
    resolution = store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="r",
            status="assumption_backed",
            target_id=decision.id,
        )
    )
    claim = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.CLAIM,
            natural_key="claim",
            data={"review_status": "reviewed"},
        )
    )
    edge = store.upsert_edge(
        Edge(project_id="p", type=EdgeType.SUPPORTS, src=dataset.id, dst=claim.id)
    )
    yield store, dataset, record, resolution, claim, edge
    store.close()


def test_composite_failure_rolls_back_nodes_edges_records_resolutions_and_review(state):
    store, dataset, record, resolution, claim, edge = state
    before_nodes = store.all_nodes("p")
    with pytest.raises(ValueError, match="stop"), store.transaction():
        source = store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="new"))
        store.upsert_edge(
            Edge(project_id="p", type=EdgeType.DERIVED_FROM, src=dataset.id, dst=source.id)
        )
        store.put_records([record.model_copy(update={"data": {"value": 2}})])
        store.put_resolution(resolution)
        assert store.get_node("p", claim.id).data["review_status"] == "needs_review"
        raise ValueError("stop")
    assert store.all_nodes("p") == before_nodes
    assert store.all_edges("p") == [edge]
    assert store.records("p", dataset.id) == [record]
    assert store.resolutions("p", dataset.id)["r"] == resolution


def test_caught_nested_failure_preserves_outer_transaction_and_rolls_back_inner_work(state):
    store, dataset, record, resolution, claim, _ = state
    with store.transaction():
        retained = store.upsert_node(
            Node(project_id="p", type=NodeType.SOURCE, natural_key="retained")
        )
        with pytest.raises(ValueError, match="nested"), store.transaction():
            store.put_records([record.model_copy(update={"data": {"value": 2}})])
            raise ValueError("nested")
        assert store.records("p", dataset.id) == [record]
        assert store.resolutions("p", dataset.id)["r"] == resolution
        assert store.get_node("p", claim.id).data["review_status"] == "reviewed"
        sibling = store.upsert_node(
            Node(project_id="p", type=NodeType.SOURCE, natural_key="sibling")
        )
    reopened = Store(store.path)
    try:
        assert reopened.get_node("p", retained.id) == retained
        assert reopened.get_node("p", sibling.id) == sibling
    finally:
        reopened.close()


def test_interrupt_rolls_back_transaction_and_connection_remains_usable(state):
    store, dataset, record, _, _, _ = state
    with pytest.raises(KeyboardInterrupt), store.transaction():
        store.put_records([record.model_copy(update={"data": {"value": 2}})])
        raise KeyboardInterrupt
    assert store.records("p", dataset.id) == [record]
    store.put_records([record.model_copy(update={"data": {"value": 3}})])
    assert store.records("p", dataset.id)[0].data == {"value": 3}


def test_two_connections_competing_on_one_revision_have_one_winner(state):
    store, dataset, record, _, claim, _ = state
    expected = revision(record.data)
    ready = Barrier(2)

    def update(value):
        worker = Store(store.path)
        try:
            ready.wait(timeout=5)
            with worker.transaction():
                current = worker.records("p", dataset.id)[0].data
                desired = {"value": value}
                if check_revision(current, desired, expected):
                    worker.put_records([record.model_copy(update={"data": desired})])
            return "written"
        except RevisionConflictError:
            return "conflict"
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(update, value) for value in (2, 3)]
        assert sorted(f.result(timeout=10) for f in results) == ["conflict", "written"]
    assert store.records("p", dataset.id)[0].data["value"] in (2, 3)
    assert store.resolutions("p", dataset.id) == {}
    assert store.get_node("p", claim.id).data["review_status"] == "needs_review"


def test_stale_resolution_cannot_be_written_after_another_connection_corrects_value(state):
    store, dataset, record, resolution, _, _ = state
    expected = revision(record.data)
    other = Store(store.path)
    try:
        other.put_records([record.model_copy(update={"data": {"value": 2}})])
        with pytest.raises(RevisionConflictError), store.transaction():
            require_revision(store.records("p", dataset.id)[0].data, expected)
            store.put_resolution(resolution)
        assert store.resolutions("p", dataset.id) == {}
    finally:
        other.close()
