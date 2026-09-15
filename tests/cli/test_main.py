import json

import pytest

from xyle_trace.cli.main import main


def test_init_creates_a_database(tmp_path):
    db = tmp_path / "graph.db"
    assert main(["init", "--project", "p", "--db", str(db)]) == 0
    assert db.exists()


def test_validate_reports_a_contracted_dataset_with_no_records(tmp_path, capsys):
    db = tmp_path / "graph.db"
    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("- dataset: d\n  require:\n    every_row: [evidence_backed]\n")
    main(["init", "--project", "p", "--db", str(db)])
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 1
    assert "dataset_not_found" in capsys.readouterr().out


def test_validate_passes_when_no_contracts_are_declared(tmp_path, capsys):
    db = tmp_path / "graph.db"
    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("[]\n")
    main(["init", "--project", "p", "--db", str(db)])
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 0
    assert "no violations" in capsys.readouterr().out.lower()


def test_validate_reports_but_does_not_fail_on_an_ungoverned_dataset(tmp_path, capsys):
    from xyle_trace.models.entities import Node, NodeType, Record
    from xyle_trace.store.sqlite_store import Store

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    store = Store(db)
    store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="scratch_notes"))
    store.put_records([Record(project_id="p", dataset_id="scratch_notes", key="row0")])
    store.close()

    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("[]\n")
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 0
    assert "ungoverned_dataset" in capsys.readouterr().out


def test_validate_exits_nonzero_when_records_are_unresolved(tmp_path, capsys):
    from xyle_trace.models.entities import Node, NodeType, Record
    from xyle_trace.store.sqlite_store import Store

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    store = Store(db)
    store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="d"))
    store.put_records([Record(project_id="p", dataset_id="d", key="row0")])
    store.close()

    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("- dataset: d\n  require:\n    every_row: [evidence_backed]\n")
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 1
    assert "unresolved_record" in capsys.readouterr().out


def _cli_evidence_chain(db, *, anchor, snapshot_text):
    """Register a dataset/record backed by evidence anchored into real snapshot bytes.

    Snapshot bytes are written under ``<db.parent>/snapshots/<digest>``, matching
    where the CLI's ``validate`` command derives its snapshot root from the
    database path (a sibling of ``.lineage/graph.db``).
    """
    from xyle_trace.core.hashing import hash_bytes
    from xyle_trace.models.entities import Node, NodeType, Record, Resolution
    from xyle_trace.store.sqlite_store import Store

    store = Store(db)
    store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="d"))
    store.put_records([Record(project_id="p", dataset_id="d", key="row0")])
    data = snapshot_text.encode("utf-8")
    content_hash = hash_bytes(data)
    snapshot_root = db.parent / "snapshots"
    snapshot_root.mkdir(exist_ok=True)
    (snapshot_root / content_hash.removeprefix("sha256:")).write_bytes(data)
    source = store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="source"))
    snapshot = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="snapshot",
            data={"source_id": source.id, "content_hash": content_hash},
        )
    )
    evidence = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.EVIDENCE_LINK,
            natural_key="ev",
            data={
                "snapshot_id": snapshot.id,
                "locator": {"kind": "table", "value": "Table 1", "anchor": anchor},
            },
        )
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id="d",
            record_key="row0",
            status="evidence_backed",
            target_id=evidence.id,
        )
    )
    store.close()


def test_validate_confirms_a_verified_locator_anchor_that_resolves(tmp_path, capsys):
    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    _cli_evidence_chain(db, anchor="1,234 items", snapshot_text="Sample A count: 1,234 items")

    contracts = tmp_path / "contracts.yaml"
    contracts.write_text(
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n"
        "    evidence: verified_locator\n"
    )
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 0
    assert "no violations" in capsys.readouterr().out.lower()


def test_validate_reports_a_verified_locator_anchor_that_does_not_resolve(tmp_path, capsys):
    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    _cli_evidence_chain(db, anchor="9,999 items", snapshot_text="Sample A count: 1,234 items")

    contracts = tmp_path / "contracts.yaml"
    contracts.write_text(
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n"
        "    evidence: verified_locator\n"
    )
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 1
    assert "locator_not_found" in capsys.readouterr().out


def test_export_writes_a_json_file(tmp_path):
    db = tmp_path / "graph.db"
    out = tmp_path / "graph.json"
    main(["init", "--project", "p", "--db", str(db)])
    assert main(["export", "--project", "p", "--db", str(db), "--out", str(out)]) == 0
    assert "nodes" in json.loads(out.read_text())


@pytest.mark.parametrize("target", ["database", "symlink", "hardlink", "sidecar"])
def test_export_cannot_destroy_its_database_or_sidecars(tmp_path, target):
    import os
    import sqlite3

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    destination = db
    if target == "symlink":
        destination = tmp_path / "alias"
        destination.symlink_to(db)
    elif target == "hardlink":
        destination = tmp_path / "alias"
        os.link(db, destination)
    elif target == "sidecar":
        destination = tmp_path / "graph.db-wal"
    assert main(["export", "--project", "p", "--db", str(db), "--out", str(destination)]) == 2
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM nodes").fetchone() == (1,)


def test_html_export_and_failed_publication_preserve_existing_output(tmp_path, monkeypatch):
    import os

    db, destination = tmp_path / "graph.db", tmp_path / "lineage.html"
    main(["init", "--project", "p", "--db", str(db)])
    args = [
        "export",
        "--project",
        "p",
        "--db",
        str(db),
        "--format",
        "html",
        "--out",
        str(destination),
    ]
    assert main(args) == 0
    before = destination.read_bytes()
    assert b"Lineage explorer" in before

    def fail(*args):
        raise PermissionError("simulated publication failure")

    monkeypatch.setattr(os, "replace", fail)
    assert main(args) == 2
    assert destination.read_bytes() == before
    assert list(tmp_path.glob(".export-*")) == []


def test_export_rejects_unknown_project_without_writing(tmp_path):
    db, destination = tmp_path / "graph.db", tmp_path / "lineage.json"
    main(["init", "--project", "p", "--db", str(db)])
    assert main(["export", "--project", "missing", "--db", str(db), "--out", str(destination)]) == 2
    assert not destination.exists()


def test_explain_reports_an_unknown_node(tmp_path, capsys):
    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    code = main(["explain", "--project", "p", "--db", str(db), "--node", "source:missing"])
    assert code == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_show_returns_captured_snapshot_metadata(tmp_path, capsys):
    from xyle_trace.core.hashing import hash_bytes
    from xyle_trace.models.entities import Node, NodeType
    from xyle_trace.store.sqlite_store import Store

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    capsys.readouterr()

    payload = b"Sample A count was 1,234 items"
    content_hash = hash_bytes(payload)
    snapshots = tmp_path / ".lineage" / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / content_hash.removeprefix("sha256:")).write_bytes(payload)

    store = Store(db)
    source = store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="source"))
    snapshot = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="snapshot",
            data={"source_id": source.id, "content_hash": content_hash},
        )
    )
    store.close()

    args = ["show", "--project", "p", "--db", str(db), "--root", str(tmp_path)]
    code = main([*args, "--snapshot", snapshot.id])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["verified"] is True
    assert out["size_bytes"] == len(payload)
    assert "text" not in out

    main([*args, "--snapshot", snapshot.id, "--text"])
    out = json.loads(capsys.readouterr().out)
    assert out["text"] == payload.decode()


def test_show_reports_an_unknown_snapshot(tmp_path, capsys):
    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    code = main(
        [
            "show",
            "--project",
            "p",
            "--db",
            str(db),
            "--root",
            str(tmp_path),
            "--snapshot",
            "source_snapshot:missing",
        ]
    )
    assert code == 2
    assert "not found" in capsys.readouterr().out.lower()


def test_ledger_reports_standing_assumptions_and_filters_unbacked_ones(tmp_path, capsys):
    from xyle_trace.models.entities import Node, NodeType, Record, Resolution
    from xyle_trace.store.sqlite_store import Store

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    capsys.readouterr()

    store = Store(db)
    dataset = store.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key="d"))
    store.put_records(
        [
            Record(project_id="p", dataset_id=dataset.id, key="row0"),
            Record(project_id="p", dataset_id=dataset.id, key="row1"),
        ]
    )

    standing = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.DECISION,
            natural_key="assume-1",
            data={
                "kind": "assumption",
                "assumption_type": "interpolated",
                "rationale": "no source yet",
                "provisional": True,
                "metadata": {"replacement_action": "capture the release"},
            },
        )
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="row0",
            status="assumption_backed",
            target_id=standing.id,
        )
    )

    # row1 was originally backed by assume-2, but is now evidence_backed by
    # something else entirely; the resolution row only holds its current
    # target, so assume-2's own backing is invisible to the ledger.
    unbacked = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.DECISION,
            natural_key="assume-2",
            data={
                "kind": "assumption",
                "assumption_type": "interpolated",
                "rationale": "no other source yet",
                "provisional": True,
                "metadata": {"replacement_action": "capture the other release"},
            },
        )
    )
    evidence = store.upsert_node(
        Node(project_id="p", type=NodeType.EVIDENCE_LINK, natural_key="ev-row1")
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="row1",
            status="evidence_backed",
            target_id=evidence.id,
        )
    )
    store.close()

    code = main(["ledger", "--project", "p", "--db", str(db)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    statuses = {entry["natural_key"]: entry["status"] for entry in out}
    assert statuses == {"assume-1": "standing", "assume-2": "unbacked"}
    assert all("retired_by" not in entry for entry in out)

    code = main(["ledger", "--project", "p", "--db", str(db), "--open-only"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [entry["natural_key"] for entry in out] == ["assume-1"]
    assert unbacked.id not in [entry["decision_id"] for entry in out]


def test_validate_against_a_nonexistent_db_does_not_create_one(tmp_path, capsys):
    db = tmp_path / "does-not-exist.db"
    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("- dataset: d\n  require:\n    every_row: [evidence_backed]\n")
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 2
    assert not db.exists()
    assert str(db) in capsys.readouterr().out


def test_export_against_a_nonexistent_db_does_not_create_one(tmp_path):
    db = tmp_path / "does-not-exist.db"
    out = tmp_path / "graph.json"
    code = main(["export", "--project", "p", "--db", str(db), "--out", str(out)])
    assert code == 2
    assert not db.exists()


def test_explain_against_a_nonexistent_db_does_not_create_one(tmp_path):
    db = tmp_path / "does-not-exist.db"
    code = main(["explain", "--project", "p", "--db", str(db), "--node", "source:missing"])
    assert code == 2
    assert not db.exists()


def test_validate_with_a_missing_contracts_file_returns_2(tmp_path, capsys):
    db = tmp_path / "graph.db"
    contracts = tmp_path / "does-not-exist.yaml"
    main(["init", "--project", "p", "--db", str(db)])
    code = main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)])
    assert code == 2
    assert "failed to load contracts" in capsys.readouterr().out.lower()


def test_validate_handles_yaml_syntax_errors(tmp_path, capsys):
    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    contracts = tmp_path / "contracts.yaml"
    contracts.write_text("- dataset: [\n")
    assert main(["validate", "--project", "p", "--db", str(db), "--contracts", str(contracts)]) == 2
    assert "failed to load contracts" in capsys.readouterr().out


def test_cli_rejects_future_schema_without_changing_it(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "graph.db"
    main(["init", "--project", "p", "--db", str(db)])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")
    assert (
        main(["export", "--project", "p", "--db", str(db), "--out", str(tmp_path / "out.json")])
        == 2
    )
    assert "unsupported database schema version 2" in capsys.readouterr().out
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_new_creates_a_project_a_host_can_open(tmp_path):
    project = tmp_path / "analysis"
    assert main(["new", "--project", "analysis", "--path", str(project)]) == 0

    assert (project / ".lineage" / "graph.db").exists()
    assert (project / "contracts.yaml").exists()
    assert (project / "CLAUDE.md").exists()
    installed = sorted(p.parent.name for p in project.glob(".claude/skills/*/SKILL.md"))
    assert installed == ["claims", "extraction", "lineage", "reproducible-runs", "source-discovery"]

    config = json.loads((project / ".mcp.json").read_text())
    server = config["mcpServers"]["xyle_trace"]
    assert server["type"] == "stdio"
    assert str(project / ".lineage" / "graph.db") in server["args"]
    assert "analysis" in server["args"]


def test_new_seeds_a_contract_the_project_immediately_reports(tmp_path, capsys):
    project = tmp_path / "analysis"
    main(["new", "--project", "analysis", "--path", str(project)])
    capsys.readouterr()

    code = main(
        [
            "validate",
            "--project",
            "analysis",
            "--db",
            str(project / ".lineage" / "graph.db"),
            "--contracts",
            str(project / "contracts.yaml"),
        ]
    )
    assert code == 1
    assert "dataset_not_found" in capsys.readouterr().out


def test_new_writes_codex_configuration_instead(tmp_path):
    project = tmp_path / "analysis"
    assert main(["new", "--project", "analysis", "--path", str(project), "--host", "codex"]) == 0

    assert (project / "AGENTS.md").exists()
    assert (project / ".agents" / "skills" / "lineage" / "SKILL.md").exists()
    assert "mcp_servers.xyle_trace" in (project / ".codex" / "config.toml").read_text()
    assert not (project / ".mcp.json").exists()
    assert not (project / "CLAUDE.md").exists()


def test_new_refuses_to_write_into_a_nonempty_directory(tmp_path, capsys):
    project = tmp_path / "analysis"
    project.mkdir()
    (project / "existing.txt").write_text("keep me")

    assert main(["new", "--project", "analysis", "--path", str(project)]) == 2
    assert "existing.txt" in [p.name for p in project.iterdir()]
    assert not (project / ".lineage").exists()
    assert "not empty" in capsys.readouterr().out


def test_new_keeps_private_evidence_out_of_git_by_default(tmp_path):
    root = tmp_path / "project"
    assert main(["new", "--project", "p", "--path", str(root), "--host", "both"]) == 0
    assert ".lineage/" in (root / ".gitignore").read_text()
    assert ".env" in (root / ".gitignore").read_text()
    for name in ("CLAUDE.md", "AGENTS.md"):
        guidance = (root / name).read_text()
        assert "Keep `.lineage/` in version control" not in guidance
        assert "private backup" in guidance
