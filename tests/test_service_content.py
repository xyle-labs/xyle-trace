"""Content mode reads captured snapshot bytes back through the public surface.

Capture was one-way: nothing in the service handed the bytes back, so the
reference consumer opened `.lineage/snapshots/<hash>` by raw path. These tests
cover the new `"content"` mode on `Service.query`, including the case that
matters most: a tampered blob must come back `verified: false`, not raise,
because a snapshot that fails its hash check is still worth inspecting.
"""

from pathlib import Path

import pytest

from xyle_trace.core.hashing import hash_bytes
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store

PROJECT = "p"


def _build(tmp_path, payload: bytes):
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id=PROJECT, type=NodeType.PROJECT, natural_key=PROJECT))
    source = store.upsert_node(
        Node(project_id=PROJECT, type=NodeType.SOURCE, natural_key="source")
    )
    content_hash = hash_bytes(payload)
    snapshot_dir = tmp_path / ".lineage" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / content_hash.removeprefix("sha256:")).write_bytes(payload)
    snapshot = store.upsert_node(
        Node(
            project_id=PROJECT,
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="snapshot",
            data={"source_id": source.id, "content_hash": content_hash},
        )
    )
    service = Service(store, PROJECT, root=tmp_path, contracts=[])
    return service, snapshot.id, store


@pytest.fixture
def service_with_snapshot(tmp_path):
    payload = b"Sample A count was 1,234 items"
    service, snapshot_id, store = _build(tmp_path, payload)
    yield service, snapshot_id, payload
    store.close()


@pytest.fixture
def service_with_tampered_snapshot(tmp_path):
    payload = b"Sample A count was 1,234 items"
    service, snapshot_id, store = _build(tmp_path, payload)
    digest = hash_bytes(payload).removeprefix("sha256:")
    (tmp_path / ".lineage" / "snapshots" / digest).write_bytes(
        b"tampered bytes that do not match the recorded hash"
    )
    yield service, snapshot_id
    store.close()


def test_content_mode_returns_a_verified_path(service_with_snapshot):
    service, snapshot_id, expected_bytes = service_with_snapshot
    result = service.query({"mode": "content", "node_id": snapshot_id})
    assert result.content.verified is True
    assert result.content.size_bytes == len(expected_bytes)
    assert Path(result.content.path).read_bytes() == expected_bytes


def test_content_mode_reports_a_tampered_snapshot(service_with_tampered_snapshot):
    service, snapshot_id = service_with_tampered_snapshot
    result = service.query({"mode": "content", "node_id": snapshot_id})
    assert result.content.verified is False


def test_content_mode_returns_text_for_decodable_bytes(service_with_snapshot):
    service, snapshot_id, expected_bytes = service_with_snapshot
    result = service.query({"mode": "content", "node_id": snapshot_id})
    assert result.content.text == expected_bytes.decode()


def test_content_mode_rejects_a_non_snapshot_node(service_with_snapshot):
    service, _, _ = service_with_snapshot
    with pytest.raises(ServiceError) as excinfo:
        service.query({"mode": "content", "node_id": "dataset:0000000000000000"})
    assert excinfo.value.error.code == "not_found"  # ServiceError stores an ErrorDetail, not a bare code


def test_content_mode_includes_the_requested_node(service_with_snapshot):
    """Every query response carries the requested node alongside reached nodes.

    Content mode reaches no other nodes, but the snapshot itself must still be
    in `nodes` (docs/core-contract.md's invariant) so a caller does not need a
    second round trip just to see the node it already asked for by ID.
    """
    service, snapshot_id, _ = service_with_snapshot
    result = service.query({"mode": "content", "node_id": snapshot_id})
    assert [state.node.id for state in result.nodes] == [snapshot_id]


def test_content_mode_handles_a_zero_byte_snapshot(tmp_path):
    """A blank captured file must not crash the response building `ContentRef`.

    `ContentRef.text` used to reuse the shared `Text` alias, which forbids the
    empty string (`min_length=1`); an empty decoded preview would then fail
    pydantic validation from inside the service and blame the *request* for a
    perfectly valid, if uninteresting, captured file.
    """
    service, snapshot_id, store = _build(tmp_path, b"")
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.size_bytes == 0
        assert result.content.text == ""
    finally:
        store.close()


def test_content_mode_handles_a_whitespace_only_snapshot(tmp_path):
    """A file that is just whitespace is legitimate content, not an error."""
    payload = b"\n  \n"
    service, snapshot_id, store = _build(tmp_path, payload)
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.text == payload.decode()
    finally:
        store.close()


def test_content_mode_returns_the_full_text_of_a_snapshot_larger_than_64kib(tmp_path):
    """`text` must never be a silent prefix beside `verified: true`.

    A 64 KiB truncation used to drop the tail of larger captures without any
    marker, so a `verified_locator` anchor near the end could pass while
    content-mode's own `text` never showed it.
    """
    payload = b"x,y\n" + b"1,2\n" * 20000
    payload += b"FINAL ANCHOR 9,999 items\n"
    assert len(payload) > 64 * 1024
    service, snapshot_id, store = _build(tmp_path, payload)
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.size_bytes == len(payload)
        assert result.content.text == payload.decode()
        assert "FINAL ANCHOR 9,999 items" in result.content.text
    finally:
        store.close()


def test_content_mode_decodes_utf16_correctly(tmp_path):
    """A UTF-16 capture must decode as UTF-16, not be forced through UTF-8.

    Detection (via `verify_anchor`/`decode_text`) accepts UTF-16 via its BOM;
    `text` must be decoded the same way, or `verified: true` sits next to
    mojibake instead of the actual content.
    """
    payload = "Sample A count was 1,234 items".encode("utf-16")
    service, snapshot_id, store = _build(tmp_path, payload)
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.text == "Sample A count was 1,234 items"
        assert "1,234 items" in result.content.text
    finally:
        store.close()


def test_content_mode_decodes_latin1_correctly(tmp_path):
    """A cp1252/latin-1 capture must decode as latin-1, not be forced through UTF-8."""
    payload = "Café count was 500 items".encode("latin-1")
    service, snapshot_id, store = _build(tmp_path, payload)
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.text == "Café count was 500 items"
    finally:
        store.close()


def test_content_mode_preserves_leading_and_trailing_whitespace(tmp_path):
    """`text` must be the decoded bytes byte-for-byte, not the `Text`-stripped form.

    `Text` (used elsewhere in operations.py) strips leading/trailing whitespace,
    which would silently hand back different content than what `verified` was
    computed against — a CSV/JSON blob's exact bytes matter to a consumer that
    trusts `verified: true`.
    """
    payload = b"  a,b\n1,2\n\n"
    service, snapshot_id, store = _build(tmp_path, payload)
    try:
        result = service.query({"mode": "content", "node_id": snapshot_id})
        assert result.content.verified is True
        assert result.content.text == payload.decode()
        assert result.content.text != payload.decode().strip()
    finally:
        store.close()


@pytest.mark.parametrize("component", ["blob", "snapshots", ".lineage"])
def test_content_rejects_symlinked_storage(tmp_path, component):
    root = tmp_path / "project"
    root.mkdir()
    payload = b"original public evidence"
    service, snapshot_id, store = _build(root, payload)
    digest = hash_bytes(payload).removeprefix("sha256:")
    path = root / ".lineage" / "snapshots" / digest
    if component != "blob":
        path = root / ".lineage"
        if component == "snapshots":
            path /= "snapshots"
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    try:
        with pytest.raises(ServiceError):
            service.query({"mode": "content", "node_id": snapshot_id})
    finally:
        store.close()
