from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.snapshots import SnapshotCapture
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType
from xyle_trace.queries.health import project_health
from xyle_trace.store.sqlite_store import Store


def test_policy_pass_does_not_hide_changed_output_or_review(tmp_path):
    store = Store(tmp_path / ".lineage" / "lineage.db")
    artifact = Node(
        project_id="p", type=NodeType.ARTIFACT, natural_key="report", data={"path": "report.txt"}
    )
    claim = Node(
        project_id="p",
        type=NodeType.CLAIM,
        natural_key="claim",
        data={"review_status": "needs_review"},
    )
    run = Node(
        project_id="p",
        type=NodeType.RUN,
        natural_key="run",
        data={
            "capture_mode": "reported",
            "status": "succeeded",
            "output_file_hashes": {artifact.id: hash_bytes(b"original")},
            "capture_gaps": ["execution_not_observed"],
        },
    )
    for node in [artifact, claim, run]:
        store.upsert_node(node)
    store.upsert_edge(Edge(project_id="p", type=EdgeType.PRODUCED, src=run.id, dst=artifact.id))
    (tmp_path / "report.txt").write_bytes(b"changed")
    before = [n.model_dump() for n in store.all_nodes("p")]
    result = project_health(store, "p", tmp_path, [])
    assert result["policy"]["valid"] is True
    assert result["integrity"]["ok"] is False
    assert result["integrity"]["artifacts"][0]["status"] == "changed"
    assert result["review"]["pending_claim_ids"] == [claim.id]
    assert result["runs"]["reported"] == 1
    assert result["capture"]["gaps"]
    assert [n.model_dump() for n in store.all_nodes("p")] == before
    store.close()


def test_retained_blobs_missing_corrupt_and_managed_references(tmp_path):
    store = Store(tmp_path / "store.db")
    blob = SnapshotCapture(tmp_path).bytes(b"kept")
    source = Node(project_id="p", type=NodeType.SOURCE, natural_key="source")
    snapshot = Node(
        project_id="p",
        type=NodeType.SOURCE_SNAPSHOT,
        natural_key="snapshot",
        data={"source_id": source.id, "content_hash": blob.content_hash},
    )
    run = Node(
        project_id="p",
        type=NodeType.RUN,
        natural_key="managed",
        data={
            "capture_mode": "managed",
            "status": "failed",
            "bundle": {"path": blob.path, "content_hash": blob.content_hash},
            "output": {
                "path": ".lineage/snapshots/" + "0" * 64,
                "content_hash": "sha256:" + "0" * 64,
            },
        },
    )
    for node in [source, snapshot, run]:
        store.upsert_node(node)
    (tmp_path / blob.path).write_bytes(b"corrupt")
    result = project_health(store, "p", tmp_path, [])
    assert {item["status"] for item in result["integrity"]["retained"]} == {"corrupt", "missing"}
    assert result["runs"]["managed"] == result["runs"]["failed"] == 1
    store.close()


def test_health_explicit_receipts_and_unsafe_artifact(tmp_path):
    store = Store(tmp_path / "store.db")
    artifact = Node(
        project_id="p", type=NodeType.ARTIFACT, natural_key="bad", data={"path": "../outside"}
    )
    store.upsert_node(artifact)
    (tmp_path / "receipt.json").write_text('{"project_id":"p","run_id":"remote-run"}')
    result = project_health(store, "p", tmp_path, [], receipt_paths=["receipt.json"])
    assert result["receipts"][0]["status"] == "run_missing_locally"
    assert result["integrity"]["artifacts"][0]["status"] == "unsafe"
    assert result["capture"]["orphan_artifact_ids"] == [artifact.id]
    assert project_health(store, "p", tmp_path, [])["receipts"] == []
    store.close()


def test_latest_success_uses_observed_time_and_provisional_edges_are_gaps(tmp_path):
    store = Store(tmp_path / "store.db")
    artifact = Node(
        project_id="p", type=NodeType.ARTIFACT, natural_key="artifact", data={"path": "result"}
    )
    (tmp_path / "result").write_bytes(b"new")
    store.upsert_node(artifact)
    runs = [
        ("earlier", "2026-10-25T09:00:00+08:00", b"old"),
        ("latest", "2026-10-25T02:00:00+00:00", b"new"),
    ]
    for name, finished, data in runs:
        run = Node(
            project_id="p",
            type=NodeType.RUN,
            natural_key=name,
            data={
                "status": "succeeded",
                "finished_at": finished,
                "output_file_hashes": {artifact.id: hash_bytes(data)},
            },
        )
        store.upsert_node(run)
        store.upsert_edge(Edge(project_id="p", type=EdgeType.PRODUCED, src=run.id, dst=artifact.id))
    provisional = Node(
        project_id="p", type=NodeType.ARTIFACT, natural_key="provisional", data={"path": "result"}
    )
    store.upsert_node(provisional)
    store.upsert_edge(
        Edge(
            project_id="p",
            type=EdgeType.PRODUCED,
            src=run.id,
            dst=provisional.id,
            data={"provisional": True},
        )
    )
    result = project_health(store, "p", tmp_path, [])
    item = next(item for item in result["integrity"]["artifacts"] if item["node_id"] == artifact.id)
    assert item["status"] == "current"
    assert result["capture"]["provisional_production_artifact_ids"] == [provisional.id]
    store.close()


def test_native_run_result_receipt_missing_local_graph(tmp_path):
    store = Store(tmp_path / "store.db")
    (tmp_path / "receipt.json").write_text(
        '{"run":{"id":"run:remote","project_id":"p","type":"run"}}'
    )
    result = project_health(store, "p", tmp_path, [], receipt_paths=["receipt.json"])
    assert result["receipts"][0]["status"] == "run_missing_locally"
    store.close()


def test_doctor_reports_graph_corruption_even_without_contracts(tmp_path):
    from xyle_trace.models.entities import Record, Resolution

    store = Store(tmp_path / "store.db")
    dataset = Node(project_id="p", type=NodeType.DATASET, natural_key="rows")
    store.upsert_node(dataset)
    store.put_records([Record(project_id="p", dataset_id=dataset.id, key="a", data={"v": 1})])
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="a",
            status="assumption_backed",
            target_id="decision:missing",
        )
    )
    edge = Edge(project_id="p", type=EdgeType.DERIVED_FROM, src=dataset.id, dst="dataset:missing")
    store.upsert_edge(edge)
    before = store._conn.total_changes
    report = project_health(store, "p", tmp_path, [])
    assert report["policy"]["valid"] is True
    assert report["integrity"]["ok"] is False
    assert report["integrity"]["status"] == "failed"
    issues = report["integrity"]["graph_issues"]
    assert any(issue["kind"] == "dangling_edge" and issue["edge_id"] == edge.id for issue in issues)
    assert any(
        issue["kind"] == "missing_resolution_target" and issue["record_key"] == "a"
        for issue in issues
    )
    assert report["integrity"]["retained"] == []
    assert store._conn.total_changes == before
    store.close()


def test_doctor_handles_malformed_source_and_evidence_references(tmp_path):
    store = Store(tmp_path / "store.db")
    blob = SnapshotCapture(tmp_path).bytes(b"retained")
    snapshot = Node(
        project_id="p",
        type=NodeType.SOURCE_SNAPSHOT,
        natural_key="snapshot",
        data={"source_id": ["malformed"], "content_hash": blob.content_hash},
    )
    evidence = Node(
        project_id="p",
        type=NodeType.EVIDENCE_LINK,
        natural_key="evidence",
        data={"snapshot_id": {"malformed": True}, "provisional": False},
    )
    for node in [snapshot, evidence]:
        store.upsert_node(node)
    report = project_health(store, "p", tmp_path, [])
    assert report["integrity"]["retained"][0]["status"] == "verified"
    assert report["integrity"]["ok"] is False
    issues = report["integrity"]["graph_issues"]
    assert any(
        issue.get("node_id") == snapshot.id and issue.get("field") == "source_id"
        for issue in issues
    )
    assert any(
        issue.get("node_id") == evidence.id and issue.get("field") == "snapshot_id"
        for issue in issues
    )
    store.close()


def test_unknown_artifact_path_is_not_reported_as_corruption_or_current(tmp_path):
    store = Store(tmp_path / "store.db")
    store.upsert_node(Node(project_id="p", type=NodeType.ARTIFACT, natural_key="unlocated"))
    report = project_health(store, "p", tmp_path, [])
    assert report["integrity"]["ok"] is False
    assert report["integrity"]["status"] == "unknown"
    assert report["integrity"]["artifacts"][0]["status"] == "path_unknown"
    assert report["integrity"]["graph_issues"] == []
    store.close()


def test_orphan_record_and_resolution_relationships_are_exposed(tmp_path):
    from xyle_trace.models.entities import Record, Resolution

    store = Store(tmp_path / "store.db")
    dataset = Node(project_id="p", type=NodeType.DATASET, natural_key="rows")
    decision = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="assumption",
        data={"kind": "assumption", "assumption_type": "test", "rationale": "test"},
    )
    store.upsert_node(dataset)
    store.upsert_node(decision)
    store.put_records(
        [Record(project_id="p", dataset_id=dataset.id, key=key) for key in ("a", "b")]
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="a",
            status="assumption_backed",
            target_id=decision.id,
        )
    )
    # Simulate a partially damaged legacy store: no database FK prevents these states.
    with store.transaction():
        store._conn.execute("DELETE FROM nodes WHERE project_id = ? AND id = ?", ("p", dataset.id))
        store._conn.execute("DELETE FROM records WHERE project_id = ? AND key = ?", ("p", "a"))
    report = project_health(store, "p", tmp_path, [])
    kinds = {issue["kind"] for issue in report["integrity"]["graph_issues"]}
    assert {"invalid_record_dataset", "orphan_resolution"} <= kinds
    assert report["integrity"]["status"] == "failed"
    store.close()


def test_resolution_wrong_type_and_provisional_backing_are_integrity_failures(tmp_path):
    from xyle_trace.models.entities import Record, Resolution

    store = Store(tmp_path / "store.db")
    dataset = Node(project_id="p", type=NodeType.DATASET, natural_key="rows")
    source = Node(project_id="p", type=NodeType.SOURCE, natural_key="wrong-target")
    assumption = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="tentative",
        data={
            "kind": "assumption",
            "assumption_type": "test",
            "rationale": "Unconfirmed",
            "provisional": True,
        },
    )
    for node in (dataset, source, assumption):
        store.upsert_node(node)
    store.put_records(
        [Record(project_id="p", dataset_id=dataset.id, key=key) for key in ("a", "b")]
    )
    for key, target in [("a", source.id), ("b", assumption.id)]:
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=dataset.id,
                record_key=key,
                status="assumption_backed",
                target_id=target,
            )
        )
    report = project_health(store, "p", tmp_path, [])
    issues = report["integrity"]["graph_issues"]
    assert any(
        issue["kind"] == "resolution_target_type_mismatch" and issue["record_key"] == "a"
        for issue in issues
    )
    assert any(
        issue["kind"] == "invalid_resolution_backing" and issue["reason"] == "provisional_backing"
        for issue in issues
    )
    assert report["integrity"]["status"] == "failed"
    store.close()


def test_missing_and_wrong_type_source_references_are_not_masked_by_valid_bytes(tmp_path):
    store = Store(tmp_path / "store.db")
    blob = SnapshotCapture(tmp_path).bytes(b"retained")
    wrong = Node(project_id="p", type=NodeType.DATASET, natural_key="not-a-source")
    store.upsert_node(wrong)
    for name, target in [("absent", "source:absent"), ("wrong-type", wrong.id)]:
        store.upsert_node(
            Node(
                project_id="p",
                type=NodeType.SOURCE_SNAPSHOT,
                natural_key=name,
                data={"source_id": target, "content_hash": blob.content_hash},
            )
        )
    report = project_health(store, "p", tmp_path, [])
    assert all(item["status"] == "verified" for item in report["integrity"]["retained"])
    kinds = {issue["kind"] for issue in report["integrity"]["graph_issues"]}
    assert {"missing_reference", "reference_type_mismatch"} <= kinds
    assert report["integrity"]["status"] == "failed"
    store.close()


def test_successful_managed_run_missing_retention_is_not_verified(tmp_path):
    store = Store(tmp_path / "store.db")
    run = Node(
        project_id="p",
        type=NodeType.RUN,
        natural_key="incomplete",
        data={"capture_mode": "managed", "status": "succeeded"},
    )
    store.upsert_node(run)
    report = project_health(store, "p", tmp_path, [])
    assert report["integrity"]["ok"] is False
    missing = {
        issue["field"]
        for issue in report["integrity"]["graph_issues"]
        if issue["kind"] == "missing_retained_reference" and issue["node_id"] == run.id
    }
    assert missing == {"bundle", "output"}
    store.close()


def test_malformed_run_observations_are_reported_without_crashing(tmp_path):
    store = Store(tmp_path / "store.db")
    artifact = Node(
        project_id="p", type=NodeType.ARTIFACT, natural_key="result", data={"path": "result.txt"}
    )
    run = Node(
        project_id="p",
        type=NodeType.RUN,
        natural_key="malformed",
        data={
            "capture_mode": "reported",
            "status": "succeeded",
            "capture_gaps": 4,
            "output_file_hashes": ["not", "a", "mapping"],
        },
    )
    store.upsert_node(artifact)
    store.upsert_node(run)
    store.upsert_edge(Edge(project_id="p", type=EdgeType.PRODUCED, src=run.id, dst=artifact.id))
    (tmp_path / "result.txt").write_text("data")
    report = project_health(store, "p", tmp_path, [])
    assert report["integrity"]["status"] == "failed"
    invalid = {
        issue["field"]
        for issue in report["integrity"]["graph_issues"]
        if issue["kind"] == "invalid_run_metadata" and issue["node_id"] == run.id
    }
    assert {"capture_gaps", "output_file_hashes"} <= invalid
    assert report["integrity"]["artifacts"][0]["status"] == "unobserved"
    store.close()
