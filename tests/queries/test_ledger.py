from datetime import UTC, datetime

import pytest

from xyle_trace.models.entities import Node, NodeType, Record, Resolution
from xyle_trace.queries.ledger import replay_verification, standing_assumptions
from xyle_trace.store.sqlite_store import Store


def _put_resolution(store, project_id, dataset_id, key, target_id, status="assumption_backed"):
    """Write a record + resolution directly, bypassing dataset-node validation.

    `standing_assumptions` only consumes `store.dataset_ids` and
    `store.resolutions(..., resolve=False)`, both plain reads of the records/
    resolutions tables; neither requires a registered Dataset node.
    """
    with store.transaction():
        store._conn.execute(
            "INSERT INTO records (project_id, dataset_id, key, data) VALUES (?, ?, ?, ?)",
            (project_id, dataset_id, key, "{}"),
        )
        store._conn.execute(
            """INSERT INTO resolutions
               (project_id, dataset_id, record_key, status, target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, dataset_id, key, status, target_id, datetime.now(UTC).isoformat()),
        )


@pytest.fixture
def project_with_assumption(tmp_path):
    store = Store(tmp_path / "graph.db")
    project_id = "p"
    assumption = store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.DECISION,
            natural_key="assume-pop",
            data={
                "kind": "assumption",
                "assumption_type": "interpolated",
                "rationale": "no 2024 census yet",
                "provisional": True,
                "metadata": {"replacement_action": "capture the PSA release"},
            },
        )
    )
    _put_resolution(store, project_id, "dataset:aaaaaaaaaaaaaaaa", "pop-2024", assumption.id)
    yield store, project_id
    store.close()


@pytest.fixture
def project_with_unbacked_assumption(tmp_path):
    """The assumption's record was rebound to evidence_backed elsewhere.

    Nothing in the graph records that a correction did this — the resolution
    row only holds its current target — so this must land as "unbacked", not
    "retired": the graph genuinely cannot tell the two apart.
    """
    store = Store(tmp_path / "graph.db")
    project_id = "p"
    assumption = store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.DECISION,
            natural_key="assume-superseded",
            data={
                "kind": "assumption",
                "assumption_type": "interpolated",
                "rationale": "no source yet",
                "provisional": True,
                "metadata": {"replacement_action": "capture the release"},
            },
        )
    )
    _put_resolution(
        store,
        project_id,
        "dataset:cccccccccccccccc",
        "rec-2",
        "evidence_link:dddddddddddddddd",
        status="evidence_backed",
    )
    yield store, project_id, assumption.id
    store.close()


@pytest.fixture
def project_with_replay(tmp_path):
    store = Store(tmp_path / "graph.db")
    project_id = "p"
    original = store.upsert_node(
        Node(project_id=project_id, type=NodeType.RUN, natural_key="run-1", data={})
    )
    replay = store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.RUN,
            natural_key="run-1-replay",
            data={"replay_of": original.id, "replay_verified": True},
        )
    )
    yield store, project_id, original.id, replay.id
    store.close()


@pytest.fixture
def project_with_failed_replay(tmp_path):
    store = Store(tmp_path / "graph.db")
    project_id = "p"
    original = store.upsert_node(
        Node(project_id=project_id, type=NodeType.RUN, natural_key="run-1", data={})
    )
    store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.RUN,
            natural_key="run-1-replay",
            data={"replay_of": original.id, "replay_verified": False},
        )
    )
    yield store, project_id, original.id
    store.close()


def test_assumption_backing_a_record_is_standing(project_with_assumption):
    store, project_id = project_with_assumption
    [entry] = standing_assumptions(store, project_id)
    assert entry["status"] == "standing"
    assert "retired_by" not in entry
    assert entry["backs"] == [{"dataset_id": "dataset:aaaaaaaaaaaaaaaa", "record_key": "pop-2024"}]
    assert entry["replacement_action"] == "capture the PSA release"


def test_assumption_with_no_current_backing_is_unbacked(project_with_unbacked_assumption):
    store, project_id, assumption_id = project_with_unbacked_assumption
    [entry] = standing_assumptions(store, project_id)
    assert entry["decision_id"] == assumption_id
    assert entry["status"] == "unbacked"
    assert entry["backs"] == []
    assert "retired_by" not in entry


def test_rebinding_after_a_correction_is_standing_not_contradictory(tmp_path):
    """Bind an assumption, correct the record, then rebind the same assumption.

    `Store.put_records` deletes a record's resolution whenever a correction
    rewrites its value, so after the correction the assumption briefly backs
    nothing. Once it is rebound to the (now corrected) record, it is
    load-bearing again right now — the result must be "standing", not a
    contradictory status that both claims a correction retired it and lists
    it as currently backing that exact record.
    """
    store = Store(tmp_path / "graph.db")
    project_id = "p"
    dataset = store.upsert_node(Node(project_id=project_id, type=NodeType.DATASET, natural_key="d"))
    store.put_records(
        [Record(project_id=project_id, dataset_id=dataset.id, key="rec-1", data={"value": 1})]
    )

    assumption = store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.DECISION,
            natural_key="assume-1",
            data={
                "kind": "assumption",
                "assumption_type": "interpolated",
                "rationale": "no source yet",
                "provisional": True,
                "metadata": {},
            },
        )
    )
    store.put_resolution(
        Resolution(
            project_id=project_id,
            dataset_id=dataset.id,
            record_key="rec-1",
            status="assumption_backed",
            target_id=assumption.id,
        )
    )

    # A correction rewrites the record's value. put_records deletes the
    # resolution that pointed at the assumption as a side effect.
    store.upsert_node(
        Node(
            project_id=project_id,
            type=NodeType.DECISION,
            natural_key="correct-1",
            data={
                "kind": "correction",
                "rationale": "better source found",
                "changes": [{"dataset_id": dataset.id, "key": "rec-1"}],
                "metadata": {},
            },
        )
    )
    store.put_records(
        [Record(project_id=project_id, dataset_id=dataset.id, key="rec-1", data={"value": 2})]
    )
    assert store.resolutions(project_id, dataset.id, resolve=False) == {}

    # The same assumption is rebound to the now-corrected record.
    store.put_resolution(
        Resolution(
            project_id=project_id,
            dataset_id=dataset.id,
            record_key="rec-1",
            status="assumption_backed",
            target_id=assumption.id,
        )
    )

    [entry] = standing_assumptions(store, project_id)
    assert entry["decision_id"] == assumption.id
    assert entry["status"] == "standing"
    assert entry["backs"] == [{"dataset_id": dataset.id, "record_key": "rec-1"}]
    assert "retired_by" not in entry
    store.close()


def test_replay_maps_forward_from_the_original_run(project_with_replay):
    store, project_id, original_id, replay_id = project_with_replay
    assert replay_verification(store, project_id) == {original_id: [replay_id]}


def test_failed_replay_does_not_count_as_verification(project_with_failed_replay):
    store, project_id, _original_id = project_with_failed_replay
    assert replay_verification(store, project_id) == {}
