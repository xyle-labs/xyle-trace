"""The base install can capture typed provenance without an MCP host."""

import io
import json
import tomllib

import pytest

from xyle_trace.cli.main import main


def test_new_writes_discoverable_portable_configuration(tmp_path):
    root = tmp_path / "project"
    assert main(["new", "--project", "sample", "--path", str(root)]) == 0
    assert json.loads((root / ".lineage/config.json").read_text()) == {
        "project": "sample",
        "db": ".lineage/graph.db",
        "root": ".",
        "contracts": "contracts.yaml",
    }


def test_codex_configuration_supports_non_bmp_unicode(tmp_path):
    root = tmp_path / "research-🌱"
    assert main(["new", "--project", "study-🌱", "--path", str(root), "--host", "codex"]) == 0
    config = tomllib.loads((root / ".codex/config.toml").read_text())
    assert "study-🌱" in config["mcp_servers"]["xyle_trace"]["args"]


@pytest.fixture
def capture(tmp_path, capsys):
    root = tmp_path / "project"
    main(["new", "--project", "p", "--path", str(root)])
    (root / "contracts.yaml").write_text("[]\n")
    capsys.readouterr()

    def invoke(operation, payload, *, stdin=False):
        request = root / "request.json"
        request.write_text(json.dumps(payload))
        code = main(
            [
                "record",
                "--project",
                "p",
                "--db",
                str(root / ".lineage/graph.db"),
                "--root",
                str(root),
                "--contracts",
                str(root / "contracts.yaml"),
                "--operation",
                operation,
                "--request",
                "-" if stdin else str(request),
            ]
        )
        return code, json.loads(capsys.readouterr().out)

    return root, invoke


def test_record_captures_source_snapshot_evidence_and_correction(capture):
    root, record = capture
    (root / "observations.txt").write_text("A: 10\n")
    code, source = record(
        "register",
        {"type": "source", "natural_key": "observations", "data": {"uri": "observations.txt"}},
    )
    assert code == 0
    code, snapshot = record(
        "snapshot",
        {"kind": "local", "source_id": source["node"]["node"]["id"], "path": "observations.txt"},
    )
    assert code == 0
    assert (root / snapshot["path"]).read_text() == "A: 10\n"
    code, dataset = record(
        "register",
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [{"key": "A", "data": {"value": 10}}],
        },
    )
    assert code == 0
    row = dataset["records"][0]
    code, evidence = record(
        "link",
        {
            "kind": "evidence",
            "natural_key": "observed-A",
            "data": {
                "snapshot_id": snapshot["snapshot"]["node"]["id"],
                "provisional": False,
                "locator": {"kind": "range", "value": "Line 1"},
            },
            "records": [
                {
                    "dataset_id": row["record"]["dataset_id"],
                    "record_key": "A",
                    "expected_record_revision": row["revision"],
                }
            ],
        },
    )
    assert code == 0
    assert evidence["records"][0]["resolution"]["status"] == "evidence_backed"
    code, correction = record(
        "decide",
        {
            "kind": "correction",
            "natural_key": "correct-A",
            "rationale": "Replacement pending",
            "changes": [
                {
                    "dataset_id": row["record"]["dataset_id"],
                    "key": "A",
                    "data": {"value": 11},
                    "expected_revision": row["revision"],
                }
            ],
        },
    )
    assert code == 0
    assert correction["records"][0]["record"]["data"] == {"value": 11}
    code, conflict = record(
        "decide",
        {
            "kind": "correction",
            "natural_key": "stale-correction",
            "rationale": "Stale reader",
            "changes": [
                {
                    "dataset_id": row["record"]["dataset_id"],
                    "key": "A",
                    "data": {"value": 12},
                    "expected_revision": row["revision"],
                }
            ],
        },
    )
    assert code == 2
    assert conflict["error"]["code"] == "conflict"


def test_record_accepts_stdin_and_returns_service_schema_errors(capture, monkeypatch):
    _, record = capture
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"type": "artifact", "natural_key": "notes", "data": {"path": "notes.txt"}})
        ),
    )
    code, result = record("register", {}, stdin=True)
    assert code == 0
    assert result["node"]["node"]["natural_key"] == "notes"
    code, result = record("register", {"type": "made_up", "natural_key": "bad"})
    assert code == 2
    assert result["error"]["code"] == "invalid_request"


def test_record_run_reports_capture_without_execution(capture):
    root, record = capture
    _, dataset = record("register", {"type": "dataset", "natural_key": "inputs"})
    _, transform = record(
        "register", {"type": "transformation", "natural_key": "analysis.sum", "data": {}}
    )
    code, started = record(
        "record_run",
        {
            "action": "start",
            "execution_key": "observed",
            "transformation_id": transform["node"]["node"]["id"],
            "input_ids": [dataset["node"]["node"]["id"]],
            "code_reference": "never-execute.sh",
        },
    )
    assert code == 0
    assert started["capture_mode"] == "reported"
    assert started["reproducible"] is False
    assert not (root / "never-execute.sh").exists()


def test_record_bad_json_and_missing_database_are_clean_errors(tmp_path, capsys):
    request = tmp_path / "bad.json"
    request.write_text("{")
    args = [
        "record",
        "--project",
        "p",
        "--db",
        str(tmp_path / "absent.db"),
        "--root",
        str(tmp_path),
        "--contracts",
        str(tmp_path / "contracts.yaml"),
        "--operation",
        "register",
        "--request",
        str(request),
    ]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_request"
    request.write_text("{}")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_reference"
    assert not (tmp_path / "absent.db").exists()


def test_packaged_demo_retains_original_result_and_blocks_current_inputs(tmp_path, capsys):
    root = tmp_path / "demo"
    assert main(["demo", "--path", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["original_total"] == 30
    assert report["correction_blocked"] is True
    assert report["replay_verified"] is True
    assert report["current_validation"]["valid"] is False
    assert json.loads((root / report["replay"]["data"]["output"]["path"]).read_text()) == {
        "total": 30
    }
    assert (root / "lineage.html").is_file()
    assert (root / ".lineage/config.json").is_file()
    assert main(["demo", "--path", str(root)]) == 2


def test_record_run_finish_and_policy_gate_use_service_semantics(capture):
    root, record = capture
    _, dataset = record(
        "register",
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [{"key": "A", "data": {"value": 10}}],
        },
    )
    _, transform = record(
        "register",
        {
            "type": "transformation",
            "natural_key": "analysis.sum",
            "data": {},
        },
    )
    request = {
        "action": "start",
        "execution_key": "observed",
        "transformation_id": transform["node"]["node"]["id"],
        "input_ids": [dataset["node"]["node"]["id"]],
        "code_reference": None,
    }
    code, started = record("record_run", request)
    assert code == 0
    code, finished = record(
        "record_run",
        {
            "action": "finish",
            "run_id": started["run"]["node"]["id"],
            "expected_revision": started["run"]["revision"],
            "status": "failed",
            "failure_detail": "Synthetic external failure",
        },
    )
    assert code == 0
    assert finished["run"]["node"]["data"]["status"] == "failed"
    assert finished["reproducible"] is False
    (root / "contracts.yaml").write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n  blocks: [analysis.*]\n"
    )
    code, blocked = record("record_run", {**request, "execution_key": "blocked"})
    assert code == 2
    assert blocked["error"]["code"] == "run_blocked"


@pytest.mark.parametrize("project", ["", " p", "p "])
def test_new_rejects_project_ids_discovery_cannot_use(tmp_path, capsys, project):
    root = tmp_path / "project"
    assert main(["new", "--project", project, "--path", str(root)]) == 2
    assert "project" in capsys.readouterr().out
    assert not root.exists()


def test_new_ships_typed_cli_capture_fallback_to_both_hosts(tmp_path):
    root = tmp_path / "project"
    assert main(["new", "--project", "p", "--path", str(root), "--host", "both"]) == 0
    for guidance in (root / "CLAUDE.md", root / "AGENTS.md"):
        text = guidance.read_text()
        assert "xyle-trace record" in text
        assert "reported" in text
    for skill in (root / ".claude/skills/lineage/SKILL.md", root / ".agents/skills/lineage/SKILL.md"):
        text = skill.read_text()
        assert "xyle-trace record" in text
        assert "--request source.json" in text
        assert "--request -" in text
        assert "without the MCP" in text
        assert "does not execute" in text
