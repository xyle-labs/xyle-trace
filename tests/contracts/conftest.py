"""Shared fixtures for contract validator tests.

Follows the store/snapshot construction idioms already established in
``tests/contracts/test_validator.py``: a single project "p", a dataset named
by ``DATASET``, one record backed by an evidence link that chains to a real
snapshot node backed by a real source node.
"""

import pytest

from xyle_trace.contracts.model import Contract
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store

PROJECT = "p"
DATASET = "observations"


def _store_with_one_record(tmp_path):
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id=PROJECT, type=NodeType.DATASET, natural_key=DATASET))
    store.put_records([Record(project_id=PROJECT, dataset_id=DATASET, key="row0")])
    return store


def _snapshot_chain(store, content_hash: str) -> Node:
    source = store.upsert_node(Node(project_id=PROJECT, type=NodeType.SOURCE, natural_key="source"))
    return store.upsert_node(
        Node(
            project_id=PROJECT,
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="snapshot",
            data={"source_id": source.id, "content_hash": content_hash},
        )
    )


def _back_row0(store, evidence_node: Node) -> None:
    store.put_resolution(
        Resolution(
            project_id=PROJECT,
            dataset_id=DATASET,
            record_key="row0",
            status="evidence_backed",
            target_id=evidence_node.id,
        )
    )


@pytest.fixture
def project_with_two_datasets(tmp_path):
    """One dataset is contracted; the other holds records but no contract governs it."""
    store = _store_with_one_record(tmp_path)
    store.upsert_node(Node(project_id=PROJECT, type=NodeType.DATASET, natural_key="scratch_notes"))
    store.put_records([Record(project_id=PROJECT, dataset_id="scratch_notes", key="row0")])
    contract = Contract(dataset=DATASET, every_row=["evidence_backed", "assumption_backed"])
    yield store, PROJECT, [contract]
    store.close()


@pytest.fixture
def project_with_empty_dataset(tmp_path):
    """A dataset is registered but holds no records: a registration, not ungoverned data."""
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id=PROJECT, type=NodeType.DATASET, natural_key="registered_only"))
    yield store, PROJECT, []
    store.close()


@pytest.fixture
def project_with_unresolved_record(tmp_path):
    """One record with no resolution, contracted to require evidence_backed status."""
    store = _store_with_one_record(tmp_path)
    contract = Contract(
        dataset=DATASET,
        every_row=["evidence_backed"],
        blocks=["analysis.*"],
    )
    yield store, PROJECT, [contract]
    store.close()


@pytest.fixture
def project_with_evidence(tmp_path):
    """A fully valid evidence chain whose prose locator carries no anchor.

    Satisfies exact_locator (the locator is present) but not verified_locator
    (there is nothing to check the anchor against).
    """
    store = _store_with_one_record(tmp_path)
    content_hash = hash_bytes(b"Sample A count was 1,234 items")
    snapshot = _snapshot_chain(store, content_hash)
    node = store.upsert_node(
        Node(
            project_id=PROJECT,
            type=NodeType.EVIDENCE_LINK,
            natural_key="ev",
            data={
                "snapshot_id": snapshot.id,
                "locator": {
                    "kind": "table",
                    "value": "Page 1, table 'SAMPLE COUNTS', column 'Sample A'",
                },
            },
        )
    )
    _back_row0(store, node)
    yield store, PROJECT, node
    store.close()


def _anchored_project(tmp_path, *, anchor: str, snapshot_text: str):
    """Build a verified_locator contract whose evidence anchors into real bytes.

    The captured bytes live under a tmp_path snapshot directory, returned alongside
    the store and contracts so the caller passes it to validate_contracts explicitly
    (snapshot_root is not a shared mutable default: callers state where their bytes
    live).
    """
    store = _store_with_one_record(tmp_path)
    data = snapshot_text.encode("utf-8")
    content_hash = hash_bytes(data)
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    (snapshot_root / content_hash.removeprefix("sha256:")).write_bytes(data)
    snapshot = _snapshot_chain(store, content_hash)
    node = store.upsert_node(
        Node(
            project_id=PROJECT,
            type=NodeType.EVIDENCE_LINK,
            natural_key="ev",
            data={
                "snapshot_id": snapshot.id,
                "locator": {"kind": "table", "value": "Annex II, table 3", "anchor": anchor},
            },
        )
    )
    _back_row0(store, node)
    contract = Contract(
        dataset=DATASET,
        every_row=["evidence_backed"],
        evidence="verified_locator",
    )
    return store, PROJECT, [contract], snapshot_root


@pytest.fixture
def project_with_bad_anchor(tmp_path):
    store, project_id, contracts, snapshot_root = _anchored_project(
        tmp_path,
        anchor="9,999 items",
        snapshot_text="Sample A count was 1,234 items",
    )
    yield store, project_id, contracts, snapshot_root
    store.close()


@pytest.fixture
def project_with_good_anchor(tmp_path):
    store, project_id, contracts, snapshot_root = _anchored_project(
        tmp_path,
        anchor="1,234 items",
        snapshot_text="Sample A count was 1,234 items",
    )
    yield store, project_id, contracts, snapshot_root
    store.close()


@pytest.fixture
def project_with_orphan_artifact(tmp_path):
    """An artifact node exists but nothing points at it: no run, no transformation."""
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id=PROJECT, type=NodeType.ARTIFACT, natural_key="report.pdf"))
    yield store, PROJECT, []
    store.close()


@pytest.fixture
def project_with_run_artifact(tmp_path):
    """An artifact node produced by a run: it has an incoming edge and is explained."""
    store = Store(tmp_path / "graph.db")
    run = store.upsert_node(Node(project_id=PROJECT, type=NodeType.RUN, natural_key="run-1"))
    artifact = store.upsert_node(
        Node(project_id=PROJECT, type=NodeType.ARTIFACT, natural_key="report.pdf")
    )
    store.upsert_edge(
        Edge(project_id=PROJECT, type=EdgeType.PRODUCED, src=run.id, dst=artifact.id)
    )
    yield store, PROJECT, []
    store.close()


@pytest.fixture
def project_with_used_input_only_artifact(tmp_path):
    """An artifact only ever consumed as input, never produced by anything.

    `used_input` proves a run *read* the file, not what made it — this must
    still count as an orphan.
    """
    store = Store(tmp_path / "graph.db")
    run = store.upsert_node(Node(project_id=PROJECT, type=NodeType.RUN, natural_key="run-1"))
    artifact = store.upsert_node(
        Node(project_id=PROJECT, type=NodeType.ARTIFACT, natural_key="input.csv")
    )
    store.upsert_edge(
        Edge(project_id=PROJECT, type=EdgeType.USED_INPUT, src=run.id, dst=artifact.id)
    )
    yield store, PROJECT, []
    store.close()


@pytest.fixture
def project_with_appears_in_only_artifact(tmp_path):
    """A hand-made file only ever referenced by a claim, never produced by a run.

    This is the archetype the orphan check exists to catch: a report someone
    wrote by hand and merely cited a claim from, with no run or transformation
    behind it.
    """
    store = Store(tmp_path / "graph.db")
    claim = store.upsert_node(
        Node(project_id=PROJECT, type=NodeType.CLAIM, natural_key="claim-1", data={})
    )
    artifact = store.upsert_node(
        Node(project_id=PROJECT, type=NodeType.ARTIFACT, natural_key="hand-made-report.pdf")
    )
    store.upsert_edge(
        Edge(project_id=PROJECT, type=EdgeType.APPEARS_IN, src=claim.id, dst=artifact.id)
    )
    yield store, PROJECT, []
    store.close()


@pytest.fixture
def project_with_corrupted_snapshot(tmp_path):
    """The anchor would resolve, but the retained bytes no longer hash to what's recorded.

    Simulates on-disk corruption (or a swapped/truncated file) discovered after the
    snapshot node was written: the filename still names the right digest, but the
    bytes at that path are not the bytes that hash to it.
    """
    text = "Sample A count was 1,234 items"
    store, project_id, contracts, snapshot_root = _anchored_project(
        tmp_path, anchor="1,234 items", snapshot_text=text
    )
    digest = hash_bytes(text.encode("utf-8")).removeprefix("sha256:")
    (snapshot_root / digest).write_bytes(b"tampered bytes that do not match the recorded hash")
    yield store, project_id, contracts, snapshot_root
    store.close()
