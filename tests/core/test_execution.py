import json

import pytest

from xyle_trace.cli.main import main as init
from xyle_trace.contracts.validator import RunBlockedError
from xyle_trace.core.execution import execute
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store

PROGRAM = """import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
value = sum(row["record"]["data"]["value"] for item in payload["inputs"].values() for row in item["records"])
Path(sys.argv[2]).write_text(json.dumps({"total": value * payload["params"].get("factor", 1)}))
"""


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "graph.db"
    init(["init", "--project", "p", "--db", str(db)])
    policies = tmp_path / "contracts.yaml"
    policies.write_text("[]\n")
    store = Store(db)
    service = Service(store, "p", root=tmp_path, contracts_path=policies)
    dataset = service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [
                {"key": "a", "data": {"value": 10}},
                {"key": "b", "data": {"value": 20}},
            ],
        }
    )
    transformation = service.register(
        {"type": "transformation", "natural_key": "analysis.sum", "data": {}}
    )
    (tmp_path / "sum.py").write_text(PROGRAM)
    request = {
        "key": "first",
        "transformation_id": transformation.node.node.id,
        "input_ids": [dataset.node.node.id],
        "script": "sum.py",
        "params": {"factor": 2},
    }
    yield service, request, tmp_path
    store.close()


def test_replay_uses_retained_records_code_and_params_after_correction(setup):
    service, request, root = setup
    first = execute(service, **request)
    assert first["data"]["status"] == "succeeded" and not first["data"]["reproducible"]
    assert json.loads((root / first["data"]["output"]["path"]).read_bytes()) == {"total": 60}
    before = service.export({}).graph
    assert execute(service, **request) == first
    assert service.export({}).graph == before
    row = service.query({"mode": "explain", "node_id": request["input_ids"][0]}).records[0]
    service.decide(
        {
            "kind": "correction",
            "natural_key": "correct",
            "rationale": "New observation",
            "changes": [
                {
                    "dataset_id": row.record.dataset_id,
                    "key": row.record.key,
                    "data": {"value": 999},
                    "expected_revision": row.revision,
                }
            ],
        }
    )
    (root / "sum.py").write_text("raise RuntimeError('mutable code is not replay input')")
    (root / "contracts.yaml").write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n  blocks: [analysis.*]\n"
    )
    fresh = Store(service.store.path)
    try:
        other = Service(fresh, "p", root=root, contracts_path=root / "contracts.yaml")
        assert other.validate({}).run_blocked is None and not other.validate({}).valid
        replay = execute(other, key="replay", replay_of=first["id"])
        assert replay["data"]["status"] == "succeeded"
        assert replay["data"]["reproducible"] and replay["data"]["replay_verified"]
        assert replay["data"]["output"] == first["data"]["output"]
        assert replay["data"]["bundle"] == first["data"]["bundle"]
        assert execute(other, key="replay", replay_of=first["id"]) == replay
    finally:
        fresh.close()


@pytest.mark.parametrize("failure", ["exception", "timeout", "missing", "nonfinite", "mismatch"])
def test_failures_are_recorded_and_terminal_keys_do_not_reexecute(setup, failure):
    service, request, root = setup
    code = {
        "exception": "raise RuntimeError('failure')",
        "timeout": "while True: pass",
        "missing": "pass",
        "nonfinite": 'open("output.json", "w").write("NaN")',
        "mismatch": 'import time; open("output.json", "w").write(str(time.time_ns()))',
    }[failure]
    (root / "sum.py").write_text(code)
    request["timeout"] = 0.1 if failure == "timeout" else 5
    run = execute(service, **request)
    if failure == "mismatch":
        assert run["data"]["status"] == "succeeded"
        request = {"key": "replay", "replay_of": run["id"]}
        run = execute(service, **request)
    assert run["data"]["status"] == "failed" and run["data"]["failure_detail"]
    assert not run["data"]["reproducible"]
    assert execute(service, **request) == run


def test_gate_refuses_before_code_executes_and_key_conflicts_are_safe(setup):
    service, request, root = setup
    (root / "contracts.yaml").write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n  blocks: [analysis.*]\n"
    )
    before = service.export({}).graph
    with pytest.raises(RunBlockedError):
        execute(service, **request)
    assert service.export({}).graph == before
    (root / "contracts.yaml").write_text("[]\n")
    execute(service, **request)
    with pytest.raises(ServiceError, match="different request"):
        execute(service, **{**request, "params": {"factor": 7}})


def test_replay_refuses_corrupt_bundle_or_environment_change(setup, monkeypatch):
    service, request, root = setup
    run = execute(service, **request)
    monkeypatch.setattr("xyle_trace.core.execution.environment", dict)
    with pytest.raises(ValueError, match="environment differs"):
        execute(service, key="different", replay_of=run["id"])
    path = root / run["data"]["bundle"]["path"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash does not match"):
        execute(service, key="corrupt", replay_of=run["id"])
    with pytest.raises(ValueError, match="hash does not match"):
        execute(service, **request)


def test_file_inputs_are_retained_and_not_read_from_mutable_paths_on_replay(setup):
    service, request, root = setup
    (root / "input.txt").write_text("captured content")
    artifact = service.register(
        {"type": "artifact", "natural_key": "file", "data": {"path": "input.txt"}}
    )
    request["input_ids"] = [artifact.node.node.id]
    (root / "sum.py").write_text("""import base64,json,sys
from pathlib import Path
data=json.loads(Path(sys.argv[1]).read_text())
value=base64.b64decode(next(iter(data["inputs"].values()))["file_base64"]).decode()
Path(sys.argv[2]).write_text(json.dumps(value))
""")
    run = execute(service, **request)
    (root / "input.txt").unlink()
    replay = execute(service, key="replay", replay_of=run["id"])
    assert replay["data"]["replay_verified"]
    assert json.loads((root / replay["data"]["output"]["path"]).read_bytes()) == "captured content"


def test_changed_policy_during_capture_refuses_execution(setup, monkeypatch):
    from xyle_trace.core.snapshots import SnapshotCapture

    service, request, root = setup
    capture = SnapshotCapture.bytes

    def change_policy(self, data):
        result = capture(self, data)
        (root / "contracts.yaml").write_text("[] # changed policy receipt\n")
        return result

    monkeypatch.setattr(SnapshotCapture, "bytes", change_policy)
    before = service.export({}).graph
    with pytest.raises(ServiceError, match="policy changed"):
        execute(service, **request)
    assert service.export({}).graph == before


def test_cli_executes_and_replays_with_machine_readable_receipts(setup, capsys):
    from xyle_trace.cli.run import main

    service, request, root = setup
    capsys.readouterr()
    common = [
        "--project",
        "p",
        "--db",
        str(service.store.path),
        "--root",
        str(root),
        "--contracts",
        str(root / "contracts.yaml"),
    ]
    assert (
        main(
            [
                "execute",
                *common,
                "--key",
                "cli",
                "--transformation",
                request["transformation_id"],
                "--input",
                request["input_ids"][0],
                "--script",
                "sum.py",
            ]
        )
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assert main(["replay", *common, "--key", "cli-replay", "--run", run["id"]]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["data"]["replay_verified"]
    assert (
        main(
            [
                "execute",
                *common,
                "--key",
                "invalid",
                "--transformation",
                request["transformation_id"],
                "--input",
                request["input_ids"][0],
                "--script",
                "../outside.py",
            ]
        )
        == 2
    )
    assert "error" in json.loads(capsys.readouterr().out)
