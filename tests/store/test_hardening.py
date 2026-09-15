import sqlite3

import pytest

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import validate_contracts
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import SchemaVersionError, Store


def test_future_schema_is_rejected_without_modifying_database(tmp_path):
    path = tmp_path / "future.db"
    store = Store(path)
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 2")
    before = path.read_bytes()
    with pytest.raises(SchemaVersionError, match="version 2"):
        Store(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_known_unversioned_schema_is_stamped_without_losing_nodes(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    node = store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="source"))
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 0")
    store = Store(path)
    assert store.get_node("p", node.id) == node
    store.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_unrelated_database_is_not_adopted(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE personal (value TEXT)")
    before = path.read_bytes()
    with pytest.raises(SchemaVersionError):
        Store(path)
    assert path.read_bytes() == before


def test_dataset_aliases_resolve_to_one_project_scoped_identity(tmp_path):
    store = Store(tmp_path / "graph.db")
    try:
        dataset = store.upsert_node(
            Node(project_id="p", type=NodeType.DATASET, natural_key="inputs")
        )
        store.put_records([Record(project_id="p", dataset_id="inputs", key="r")])
        assert store.records("p", "inputs") == store.records("p", dataset.id)
        assert store.dataset_ids("p") == [dataset.id]
        for selector in ("inputs", dataset.id):
            violations = validate_contracts(
                store, "p", [Contract(dataset=selector, every_row=["evidence_backed"])]
            )
            assert violations[0].kind == "unresolved_record"
        assert store.dataset_node("q", "inputs") is None
        with pytest.raises(ValueError, match="not registered"):
            store.put_records([Record(project_id="q", dataset_id=dataset.id, key="r")])
        with pytest.raises(ValueError, match="does not exist"):
            store.put_resolution(
                Resolution(
                    project_id="p",
                    dataset_id=dataset.id,
                    record_key="missing",
                    status="evidence_backed",
                    target_id="missing",
                )
            )
    finally:
        store.close()


def test_failed_batch_rolls_back_record_edits_and_resolution_invalidation(tmp_path):
    store = Store(tmp_path / "graph.db")
    try:
        dataset = store.upsert_node(
            Node(project_id="p", type=NodeType.DATASET, natural_key="inputs")
        )
        record = Record(project_id="p", dataset_id=dataset.id, key="r", data={"value": 1})
        store.put_records([record])
        resolution = store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=dataset.id,
                record_key="r",
                status="evidence_backed",
                target_id="missing",
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
        store.upsert_edge(
            Edge(project_id="p", type=EdgeType.SUPPORTS, src=dataset.id, dst=claim.id)
        )
        with pytest.raises(ValueError, match="not registered"):
            store.put_records(
                [
                    record.model_copy(update={"data": {"value": 2}}),
                    Record(project_id="p", dataset_id="unregistered", key="r"),
                ]
            )
        assert store.records("p", dataset.id) == [record]
        assert store.resolutions("p", dataset.id)["r"] == resolution
        assert store.get_node("p", claim.id).data["review_status"] == "reviewed"
    finally:
        store.close()


def test_legacy_natural_key_records_remain_exportable_but_cannot_silently_pass(tmp_path):
    from xyle_trace.exporters.json_graph import export_graph

    path = tmp_path / "legacy.db"
    store = Store(path)
    dataset = store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="inputs"))
    store.close()
    # Reproduce the old, unconstrained storage format.
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?)", ("p", "inputs", "old", '{"value": 10}')
        )
    store = Store(path)
    try:
        store.put_records([Record(project_id="p", dataset_id=dataset.id, key="new")])
        exported = export_graph(store, "p")
        assert exported["datasets"]["inputs"]["records"][0]["key"] == "old"
        assert exported["datasets"][dataset.id]["records"][0]["key"] == "new"
        for selector in ("inputs", dataset.id):
            violations = validate_contracts(
                store, "p", [Contract(dataset=selector, every_row=["evidence_backed"])]
            )
            assert violations[0].kind == "legacy_dataset_reference"
    finally:
        store.close()
