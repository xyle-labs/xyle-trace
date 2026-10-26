import json
import zipfile

import pytest

from xyle_trace.core.handoff import export_project, import_project
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.snapshots import SnapshotCapture
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    store = Store(root / ".lineage" / "lineage.db")
    source = Node(project_id="p", type=NodeType.SOURCE, natural_key="source")
    blob = SnapshotCapture(root).bytes(b"opaque input")
    snapshot = Node(
        project_id="p",
        type=NodeType.SOURCE_SNAPSHOT,
        natural_key="snapshot",
        data={"source_id": source.id, "content_hash": blob.content_hash},
    )
    dataset = Node(project_id="p", type=NodeType.DATASET, natural_key="data")
    decision = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="decision",
        data={"kind": "assumption", "assumption_type": "test", "rationale": "test"},
    )
    for node in [source, snapshot, dataset, decision]:
        store.upsert_node(node)
    store.put_records([Record(project_id="p", dataset_id=dataset.id, key="a", data={"v": 1})])
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="a",
            status="assumption_backed",
            target_id=decision.id,
        )
    )
    store.upsert_edge(
        Edge(project_id="p", type=EdgeType.SNAPSHOT_OF, src=snapshot.id, dst=source.id)
    )
    (root / "contracts.yaml").write_text("[]\n")
    yield root, store, blob
    store.close()


def test_round_trip_preserves_graph_policy_and_opaque_content(project, tmp_path):
    root, store, blob = project
    bundle = root / "project.zip"
    result = export_project(
        store, "p", root, bundle, contracts_path=root / "contracts.yaml", include_content=True
    )
    assert result["content_complete"] is True
    restored = tmp_path / "restored"
    imported = import_project(bundle, restored)
    assert imported["project_id"] == "p"
    assert (restored / blob.path).read_bytes() == b"opaque input"
    assert (restored / "contracts.yaml").read_text() == "[]\n"
    with_store = Store(restored / ".lineage" / "graph.db", create=False)
    assert export_graph(with_store, "p") == export_graph(store, "p")
    with_store.close()


def test_metadata_only_default_is_explicitly_incomplete(project, tmp_path):
    root, store, blob = project
    bundle = root / "metadata.zip"
    result = export_project(store, "p", root, bundle)
    assert result["content_complete"] is False
    with zipfile.ZipFile(bundle) as archive:
        assert not any(name.startswith("blobs/") for name in archive.namelist())
    imported = import_project(bundle, tmp_path / "metadata")
    assert imported["content_complete"] is False
    assert not (tmp_path / "metadata" / blob.path).exists()


def test_policy_free_restore_can_discover_policy_added_later(project, tmp_path):
    from xyle_trace.mcp.__main__ import _parser, _resolve

    root, store, _ = project
    bundle = root / "policy-free.zip"
    export_project(store, "p", root, bundle)
    restored = tmp_path / "restored"
    imported = import_project(bundle, restored)

    assert imported["policy_included"] is False
    assert not (restored / "contracts.yaml").exists()
    assert "contracts" not in json.loads((restored / ".lineage/config.json").read_text())

    discovered = _resolve(_parser().parse_args([]), restored, {})
    assert discovered == (
        "p",
        restored / ".lineage/graph.db",
        restored,
        restored / "contracts.yaml",
    )
    (restored / "contracts.yaml").write_text("[]\n")
    assert _resolve(_parser().parse_args([]), restored, {}) == discovered


def test_policy_free_restore_requires_supplied_policy_before_mcp_start(project, tmp_path):
    pytest.importorskip("mcp.server")
    from xyle_trace.mcp.__main__ import _parser, _resolve
    from xyle_trace.mcp.server import ServerConfig, create_server

    root, store, _ = project
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    bundle = root / "policy-free-mcp.zip"
    export_project(store, "p", root, bundle)
    restored = tmp_path / "mcp-restored"
    import_project(bundle, restored)
    settings = _resolve(_parser().parse_args([]), restored, {})

    with pytest.raises(FileNotFoundError, match="contracts.yaml"):
        ServerConfig(*settings)
    (restored / "contracts.yaml").write_text("[]\n")
    create_server(ServerConfig(*settings))


@pytest.mark.parametrize("member", ["../escape", "/absolute", "blobs/../../escape", "unknown"])
def test_unknown_unsafe_and_duplicate_members_rejected(project, tmp_path, member):
    root, store, _ = project
    bundle = root / "bad.zip"
    export_project(store, "p", root, bundle)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(member, b"bad")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError):
        import_project(bundle, destination)
    assert not destination.exists()


def test_tampering_and_existing_destinations_rejected(project, tmp_path):
    root, store, _ = project
    bundle = root / "project.zip"
    export_project(store, "p", root, bundle, include_content=True)
    with pytest.raises(ValueError):
        export_project(store, "p", root, bundle)
    with pytest.raises(ValueError):
        import_project(bundle, root)
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    blob_name = next(name for name in entries if name.startswith("blobs/"))
    entries[blob_name] = b"wrong"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="hash"):
        import_project(bundle, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_rehashed_forged_graph_id_is_rejected(project, tmp_path):
    root, store, _ = project
    bundle = root / "project.zip"
    export_project(store, "p", root, bundle)
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    graph = json.loads(entries["graph.json"])
    graph["nodes"][0]["id"] = "forged"
    entries["graph.json"] = json.dumps(graph).encode()
    manifest = json.loads(entries["manifest.json"])
    manifest["members"]["graph.json"] = {
        "content_hash": hash_bytes(entries["graph.json"]),
        "size_bytes": len(entries["graph.json"]),
    }
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="identity"):
        import_project(bundle, tmp_path / "bad")


def test_blob_streaming_has_independent_configurable_limits(project, tmp_path, monkeypatch):
    from xyle_trace.core import handoff

    root, store, _ = project
    # Metadata allowance stays small while a blob is independently allowed above it.
    content = b"x" * 32768
    blob = SnapshotCapture(root).bytes(content)
    source = store.nodes_of_type("p", NodeType.SOURCE)[0]
    store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="large",
            data={"source_id": source.id, "content_hash": blob.content_hash},
        )
    )
    monkeypatch.setattr(handoff, "MAX_MEMBER_BYTES", 16384)
    bundle = root / "streamed.zip"
    export_project(
        store, "p", root, bundle, include_content=True, max_blob_bytes=65536, max_total_bytes=131072
    )
    import_project(bundle, tmp_path / "streamed", max_blob_bytes=65536, max_total_bytes=131072)
    assert (tmp_path / "streamed" / blob.path).read_bytes() == content
    with pytest.raises(ValueError, match="limit"):
        import_project(bundle, tmp_path / "too-small", max_blob_bytes=1024)
    with pytest.raises(ValueError, match="limit"):
        export_project(
            store, "p", root, root / "too-small.zip", include_content=True, max_blob_bytes=1024
        )
    assert not (root / "too-small.zip").exists()


def rewrite_bundle(bundle, mutate):
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    mutate(entries)
    manifest = json.loads(entries["manifest.json"])
    manifest["members"] = {
        name: {"size_bytes": len(data), "content_hash": hash_bytes(data)}
        for name, data in entries.items()
        if name != "manifest.json"
    }
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


@pytest.mark.parametrize(
    "mutation", ["edge", "resolution", "project", "dataset_shape", "nonfinite", "duplicate_json"]
)
def test_rehashed_invalid_relationships_and_metadata_are_rejected(project, tmp_path, mutation):
    root, store, _ = project
    bundle = root / "invalid.zip"
    export_project(store, "p", root, bundle)

    def change(entries):
        graph = json.loads(entries["graph.json"])
        if mutation == "edge":
            graph["edges"][0]["dst"] = "absent"
        elif mutation == "resolution":
            next(iter(next(iter(graph["datasets"].values()))["resolutions"].values()))[
                "target_id"
            ] = "absent"
        elif mutation == "project":
            graph["nodes"][0]["project_id"] = "another"
        elif mutation == "dataset_shape":
            graph["datasets"] = []
        elif mutation == "nonfinite":
            graph["nodes"][0]["data"]["bad"] = float("inf")
        elif mutation == "duplicate_json":
            entries["graph.json"] = b'{"project_id":"p","project_id":"p"}'
            return
        entries["graph.json"] = json.dumps(graph).encode()

    rewrite_bundle(bundle, change)
    with pytest.raises(ValueError):
        import_project(bundle, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_archive_symlink_and_duplicate_rejected(project, tmp_path):
    root, store, _ = project
    bundle = root / "invalid.zip"
    export_project(store, "p", root, bundle)
    with zipfile.ZipFile(bundle, "a") as archive:
        info = zipfile.ZipInfo("blobs/" + "a" * 64)
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"../../outside")
    with pytest.raises(ValueError):
        import_project(bundle, tmp_path / "invalid")
    duplicate = root / "duplicate.zip"
    export_project(store, "p", root, duplicate)
    with pytest.warns(UserWarning), zipfile.ZipFile(duplicate, "a") as archive:
        archive.writestr("graph.json", b"{}")
    with pytest.raises(ValueError, match="duplicate"):
        import_project(duplicate, tmp_path / "duplicate")


def test_export_refuses_symlinked_retention_and_protected_destinations(project, tmp_path):
    root, store, blob = project
    original = root / blob.path
    external = tmp_path / "external"
    external.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(external)
    with pytest.raises((OSError, ValueError)):
        export_project(store, "p", root, root / "bad.zip", include_content=True)
    assert not (root / "bad.zip").exists()
    for destination in [root / ".lineage" / "bundle.zip", root / ".env", tmp_path / "outside.zip"]:
        with pytest.raises(ValueError):
            export_project(store, "p", root, destination)


def test_orphan_retained_artifact_bytes_are_included(project, tmp_path):
    root, store, _ = project
    blob = SnapshotCapture(root).bytes(b"retained output")
    store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.ARTIFACT,
            natural_key="retained",
            data={"path": blob.path, "content_hash": blob.content_hash},
        )
    )
    bundle = root / "artifact.zip"
    export_project(store, "p", root, bundle, include_content=True)
    import_project(bundle, tmp_path / "artifact")
    assert (tmp_path / "artifact" / blob.path).read_bytes() == b"retained output"


def test_content_opt_in_requires_boolean(project):
    root, store, _ = project
    with pytest.raises((TypeError, ValueError)):
        export_project(store, "p", root, root / "mistaken.zip", include_content="false")
    assert not (root / "mistaken.zip").exists()


def test_policy_path_accepts_alias_above_trusted_project_root(project, tmp_path):
    root, store, _ = project
    alias = tmp_path / "parent-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    bundle = root / "aliased-policy.zip"
    policy = alias / root.name / "contracts.yaml"
    result = export_project(store, "p", root.resolve(), bundle, contracts_path=policy)
    assert result["policy_included"] is True
    with zipfile.ZipFile(bundle) as archive:
        assert archive.read("contracts.yaml") == b"[]\n"


@pytest.mark.parametrize("location", ["file", "directory", "outside"])
def test_policy_alias_fix_does_not_allow_project_symlinks_or_escape(project, tmp_path, location):
    root, store, _ = project
    if location == "file":
        policy = root / "linked-policy.yaml"
        policy.symlink_to(root / "contracts.yaml")
    elif location == "directory":
        directory = root / "policies"
        directory.mkdir()
        (directory / "contracts.yaml").write_text("[]\n")
        (root / "linked-policies").symlink_to(directory, target_is_directory=True)
        policy = root / "linked-policies" / "contracts.yaml"
    else:
        policy = tmp_path / "outside-policy.yaml"
        policy.write_text("[]\n")
    bundle = root / "blocked-policy.zip"
    with pytest.raises(ValueError):
        export_project(store, "p", root.resolve(), bundle, contracts_path=policy)
    assert not bundle.exists()


def test_zip64_directory_limits_apply_without_classic_sentinel_fields(monkeypatch):
    import io
    import struct

    from xyle_trace.core import handoff

    monkeypatch.setattr(handoff, "MAX_MEMBERS", 2)
    # ZIP readers honor this locator even when classic fields are not saturated.
    # Reject before a standard ZIP parser allocates the declared central directory.
    oversized = handoff.MAX_MEMBERS * 1024 + 1
    zip64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 1, 1, oversized, 0)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, oversized, 1)
    classic = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, 1, 0, 0)
    with pytest.raises(ValueError, match="limit"):
        handoff._check_directory(io.BytesIO(bytes(oversized) + zip64 + locator + classic))


def test_corrupt_deflated_blob_is_a_clean_failed_import(project, tmp_path):
    import struct

    root, store, _ = project
    bundle = root / "deflated.zip"
    export_project(store, "p", root, bundle, include_content=True)
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(bundle) as archive:
        info = next(item for item in archive.infolist() if item.filename.startswith("blobs/"))
    payload = bytearray(bundle.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", payload, info.header_offset + 26)
    offset = info.header_offset + 30 + name_size + extra_size
    payload[offset] = 0x07  # Invalid DEFLATE block type, while ZIP framing remains intact.
    bundle.write_bytes(payload)
    destination = tmp_path / "failed-restore"
    with pytest.raises(ValueError, match="archive"):
        import_project(bundle, destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".handoff-*")) == []


def test_valid_zip64_handoff_round_trip_with_unsaturated_classic_fields(
    project, tmp_path, monkeypatch
):
    root, store, _ = project
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 128)
    bundle = root / "zip64.zip"
    export_project(store, "p", root, bundle, include_content=True)
    destination = tmp_path / "zip64-restored"
    import_project(bundle, destination)
    restored = Store(destination / ".lineage" / "graph.db", create=False)
    try:
        assert export_graph(restored, "p") == export_graph(store, "p")
    finally:
        restored.close()
