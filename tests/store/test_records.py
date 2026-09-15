import pytest

from xyle_trace.models.entities import Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.db")
    s.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="dataset:m"))
    yield s
    s.close()


def _records(n: int) -> list[Record]:
    return [
        Record(project_id="p", dataset_id="dataset:m", key=f"row{i}", data={"i": i})
        for i in range(n)
    ]


def test_put_records_returns_count(store):
    assert store.put_records(_records(3)) == 3


def test_records_round_trip(store):
    store.put_records(_records(2))
    loaded = store.records("p", "dataset:m")
    assert [r.key for r in loaded] == ["row0", "row1"]


def test_records_are_scoped_to_project(store):
    store.put_records(_records(2))
    assert store.records("other", "dataset:m") == []


def test_put_records_is_idempotent_on_key(store):
    store.put_records(_records(2))
    store.put_records(_records(2))
    assert len(store.records("p", "dataset:m")) == 2


def test_records_start_unresolved(store):
    store.put_records(_records(2))
    assert store.unresolved_keys("p", "dataset:m") == ["row0", "row1"]


def test_resolution_removes_a_key_from_unresolved(store):
    store.put_records(_records(2))
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id="dataset:m",
            record_key="row0",
            status="evidence_backed",
            target_id="evidence_link:e1",
        )
    )
    assert store.unresolved_keys("p", "dataset:m") == ["row1"]


def test_resolutions_are_keyed_by_record_key(store):
    store.put_records(_records(1))
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id="dataset:m",
            record_key="row0",
            status="assumption_backed",
            target_id="decision:d1",
        )
    )
    resolutions = store.resolutions("p", "dataset:m")
    assert resolutions["row0"].status == "assumption_backed"


def test_resolution_can_be_replaced(store):
    store.put_records(_records(1))
    for status, target in [
        ("assumption_backed", "decision:d1"),
        ("evidence_backed", "evidence_link:e1"),
    ]:
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id="dataset:m",
                record_key="row0",
                status=status,
                target_id=target,
            )
        )
    assert store.resolutions("p", "dataset:m")["row0"].target_id == "evidence_link:e1"
