"""The managed runner delivers an ordinary file without losing its provenance."""

import json
from contextlib import closing

import pytest

from xyle_trace.cli.run import main
from xyle_trace.contracts.validator import RunBlockedError
from xyle_trace.core.execution import execute
from xyle_trace.core.hashing import hash_file
from xyle_trace.models.entities import EdgeType, Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def project(tmp_path):
    with closing(Store(tmp_path / ".lineage/graph.db")) as store:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
        policy = tmp_path / "contracts.yaml"
        policy.write_text("[]\n")
        service = Service(store, "p", root=tmp_path, contracts_path=policy)
        rows = service.register(
            {
                "type": "dataset",
                "natural_key": "inputs",
                "records": [{"key": "A", "data": {"value": 10}}],
            }
        )
        transform = service.register(
            {"type": "transformation", "natural_key": "analysis.sum", "data": {}}
        )
        (tmp_path / "sum.py").write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "d=json.loads(Path(sys.argv[1]).read_text())\n"
            'v=sum(r["record"]["data"]["value"] for x in d["inputs"].values() for r in x["records"])\n'
            'Path(sys.argv[2]).write_text(json.dumps({"total":v*d["params"].get("factor",1)}))\n'
        )
        request = {
            "key": "first",
            "transformation_id": transform.node.node.id,
            "input_ids": [rows.node.node.id],
            "script": "sum.py",
            "params": {},
        }
        yield service, request, tmp_path


def test_out_materializes_retained_bytes_with_a_separate_working_artifact(project):
    service, request, root = project
    run = execute(service, **request, out="outputs/result.json")
    working = run["data"]["working_output"]
    assert (root / working["path"]).read_bytes() == (
        root / run["data"]["output"]["path"]
    ).read_bytes()
    assert json.loads((root / working["path"]).read_bytes()) == {"total": 10}
    assert working["content_hash"] == hash_file(root / working["path"])
    assert run["data"]["output_file_hashes"] == {working["node_id"]: working["content_hash"]}
    assert run["data"]["execution_request"]["out"] == "outputs/result.json"
    assert run["data"]["finished_at"]
    alias = service.store.get_node("p", working["node_id"])
    assert alias.natural_key == "working-output:outputs/result.json"
    assert alias.data["path"] == working["path"]
    produced = service.store.edges_from("p", run["id"], [EdgeType.PRODUCED])
    assert len(produced) == 2
    assert working["node_id"] in {edge.dst for edge in produced}


@pytest.mark.parametrize("mutation", ["unchanged", "deleted", "edited"])
def test_terminal_retry_never_reexecutes_or_restores_changed_working_file(
    project, monkeypatch, mutation
):
    service, request, root = project
    run = execute(service, **request, out="result.json")
    path = root / "result.json"
    if mutation == "deleted":
        path.unlink()
    elif mutation == "edited":
        path.write_text("unrecorded edit")
    before = service.export({}).graph
    monkeypatch.setattr(
        "xyle_trace.core.execution.subprocess.run", lambda *a, **kw: pytest.fail("retry executed")
    )
    if mutation == "unchanged":
        assert execute(service, **request, out="result.json") == run
    else:
        with pytest.raises(ServiceError) as error:
            execute(service, **request, out="result.json")
        assert error.value.error.code == "conflict"
        assert (
            (not path.exists()) if mutation == "deleted" else path.read_text() == "unrecorded edit"
        )
    assert service.export({}).graph == before


def test_new_run_updates_one_verified_alias_and_preserves_old_retained_output(project):
    service, request, root = project
    original = execute(service, **request, out="result.json")
    updated = execute(
        service, **{**request, "key": "second", "params": {"factor": 2}}, out="result.json"
    )
    assert (
        updated["data"]["working_output"]["node_id"]
        == original["data"]["working_output"]["node_id"]
    )
    assert json.loads((root / "result.json").read_bytes()) == {"total": 20}
    assert json.loads((root / original["data"]["output"]["path"]).read_bytes()) == {"total": 10}
    aliases = [
        n
        for n in service.store.nodes_of_type("p", NodeType.ARTIFACT)
        if n.data["path"] == "result.json"
    ]
    assert len(aliases) == 1
    with pytest.raises(ServiceError, match="working"):
        execute(service, **request, out="result.json")


def test_out_is_part_of_execution_identity_without_changing_legacy_requests(project):
    service, request, _ = project
    run = execute(service, **request)
    assert "out" not in run["data"]["execution_request"]
    assert "working_output" not in run["data"]
    assert execute(service, **request) == run
    with pytest.raises(ServiceError, match="different request"):
        execute(service, **request, out="result.json")


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "../escape.json",
        "a/../escape.json",
        "a/./out.json",
        "/tmp/absolute.json",
        ".lineage/result.json",
        ".git/result.json",
        ".codex/result.json",
        ".agents/result.json",
        ".claude/result.json",
        ".aws/result.json",
        ".ssh/result.json",
        ".env",
        ".env.local",
        "contracts.yaml",
        "sum.py",
    ],
)
def test_unsafe_or_protected_out_is_rejected_before_any_run(project, path):
    service, request, root = project
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError)):
        execute(service, **request, out=path)
    assert service.export({}).graph == before
    assert not (root / "a").exists()


@pytest.mark.parametrize("kind", ["parent_symlink", "file_symlink", "hardlink", "untracked"])
def test_out_cannot_replace_aliases_or_untracked_files(project, kind):
    service, request, root = project
    original = root / "original.json"
    original.write_text("keep this")
    destination = "result.json"
    if kind == "parent_symlink":
        (root / "shortcut").symlink_to(root, target_is_directory=True)
        destination = "shortcut/result.json"
    elif kind == "file_symlink":
        (root / destination).symlink_to(original)
    elif kind == "hardlink":
        (root / destination).hardlink_to(original)
    else:
        (root / destination).write_text("keep this too")
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError)):
        execute(service, **request, out=destination)
    assert service.export({}).graph == before
    assert original.read_text() == "keep this"


def test_failed_and_blocked_runs_never_publish_working_output(project):
    service, request, root = project
    (root / "sum.py").write_text('raise RuntimeError("synthetic failure")')
    failed = execute(service, **request, out="outputs/result.json")
    assert failed["data"]["status"] == "failed"
    assert not (root / "outputs").exists()
    assert not failed["data"].get("working_output")
    (root / "contracts.yaml").write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n  blocks: [analysis.*]\n"
    )
    before = service.export({}).graph
    with pytest.raises(RunBlockedError):
        execute(service, **{**request, "key": "blocked"}, out="outputs/result.json")
    assert not (root / "outputs").exists()
    assert service.export({}).graph == before


def test_edit_during_execution_is_not_overwritten(project, monkeypatch):
    import xyle_trace.core.execution as runner

    service, request, root = project
    original = execute(service, **request, out="result.json")
    subprocess_run = runner.subprocess.run

    def edit_after_program(*args, **kwargs):
        result = subprocess_run(*args, **kwargs)
        (root / "result.json").write_text("concurrent unrecorded edit")
        return result

    monkeypatch.setattr(runner.subprocess, "run", edit_after_program)
    failed = execute(service, **{**request, "key": "racing"}, out="result.json")
    assert failed["data"]["status"] == "failed"
    assert not failed["data"].get("working_output")
    assert (root / "result.json").read_text() == "concurrent unrecorded edit"
    alias = service.store.get_node("p", original["data"]["working_output"]["node_id"])
    assert alias.data["content_hash"] == original["data"]["working_output"]["content_hash"]


def test_replay_can_deliver_to_a_new_working_path(project):
    service, request, root = project
    original = execute(service, **request, out="result.json")
    replay = execute(service, key="replay", replay_of=original["id"], out="replayed.json")
    assert replay["data"]["replay_verified"] is True
    assert (root / "replayed.json").read_bytes() == (root / "result.json").read_bytes()
    assert (
        replay["data"]["working_output"]["node_id"] != original["data"]["working_output"]["node_id"]
    )


def test_cli_execute_and_replay_accept_explicit_out(project, capsys):
    service, request, root = project
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
                "--out",
                "result.json",
            ]
        )
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assert (
        main(["replay", *common, "--key", "cli-replay", "--run", run["id"], "--out", "replay.json"])
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["data"]["working_output"]["path"] == "replay.json"


def test_out_cannot_replace_a_historically_used_script(project):
    service, request, root = project
    program = (root / "sum.py").read_text()
    original = execute(service, **request)
    (root / "sum.py").unlink()
    (root / "other.py").write_text(program)
    # Removing a working copy of historical code does not make its path an
    # output destination for a later execution using another script.
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError), match="script"):
        execute(service, **{**request, "key": "other", "script": "other.py"}, out="sum.py")
    assert service.export({}).graph == before
    assert not (root / "sum.py").exists()
    assert original["data"]["status"] == "succeeded"


@pytest.mark.parametrize("overlap", ["input", "source"])
def test_recorded_working_output_cannot_be_used_as_its_own_input_or_source(project, overlap):
    service, request, root = project
    first = execute(service, **request, out="result.json")
    next_request = {**request, "key": "second"}
    if overlap == "input":
        next_request["input_ids"] = [first["data"]["working_output"]["node_id"]]
    else:
        service.register(
            {"type": "source", "natural_key": "source-result", "data": {"uri": "result.json"}}
        )
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError), match="overlaps"):
        execute(service, **next_request, out="result.json")
    assert service.export({}).graph == before
    assert json.loads((root / "result.json").read_bytes()) == {"total": 10}


def test_publication_error_keeps_failed_run_but_no_working_receipt_or_file(project, monkeypatch):
    import xyle_trace.core.execution as runner

    service, request, root = project
    replace = runner.os.replace

    def fail_working_replace(source, destination):
        if destination == root / "outputs/result.json":
            raise OSError("synthetic publication failure")
        return replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_working_replace)
    failed = execute(service, **request, out="outputs/result.json")
    assert failed["data"]["status"] == "failed"
    assert "publication failed" in failed["data"]["failure_detail"]
    assert not failed["data"].get("working_output")
    assert not (root / "outputs").exists()
    assert not any(
        n.natural_key.startswith("working-output:")
        for n in service.store.nodes_of_type("p", NodeType.ARTIFACT)
    )


def test_database_failure_after_replace_is_pending_and_never_reexecutes(project, monkeypatch):
    import sqlite3

    service, request, root = project
    upsert = service.store.upsert_node

    def fail_terminal_receipt(node):
        if node.type == NodeType.RUN and node.data.get("working_output"):
            raise sqlite3.OperationalError("synthetic final database write failure")
        return upsert(node)

    monkeypatch.setattr(service.store, "upsert_node", fail_terminal_receipt)
    with pytest.raises(sqlite3.OperationalError):
        execute(service, **request, out="result.json")
    assert json.loads((root / "result.json").read_bytes()) == {"total": 10}
    pending = service.store.nodes_of_type("p", NodeType.RUN)[0]
    assert pending.data["status"] == "started"
    assert not pending.data.get("working_output")
    assert not service.store.nodes_of_type("p", NodeType.ARTIFACT)
    with pytest.raises(ServiceError, match="interrupted"):
        execute(service, **request, out="result.json")


def test_replay_mismatch_does_not_materialize_any_working_file(project, monkeypatch):
    import xyle_trace.core.execution as runner

    service, request, root = project
    original = execute(service, **request, out="result.json")
    subprocess_run = runner.subprocess.run

    def mismatching_program(*args, **kwargs):
        completed = subprocess_run(*args, **kwargs)
        (kwargs["cwd"] / "output.json").write_text('{"total": 999}')
        return completed

    monkeypatch.setattr(runner.subprocess, "run", mismatching_program)
    failed = execute(service, key="mismatch", replay_of=original["id"], out="replays/result.json")
    assert failed["data"]["status"] == "failed"
    assert failed["data"]["replay_verified"] is False
    assert not (root / "replays").exists()
    assert json.loads((root / "result.json").read_bytes()) == {"total": 10}


def test_parent_symlink_introduced_during_execution_is_refused(project, monkeypatch, tmp_path):
    import xyle_trace.core.execution as runner

    service, request, root = project
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    subprocess_run = runner.subprocess.run

    def redirect_after_program(*args, **kwargs):
        completed = subprocess_run(*args, **kwargs)
        (root / "outputs").symlink_to(external, target_is_directory=True)
        return completed

    monkeypatch.setattr(runner.subprocess, "run", redirect_after_program)
    failed = execute(service, **request, out="outputs/result.json")
    assert failed["data"]["status"] == "failed"
    assert not list(external.iterdir())
    assert not failed["data"].get("working_output")


@pytest.mark.parametrize("out", ["C:result.json", ".mcp.json", "AGENTS.md", "CLAUDE.md"])
def test_platform_and_host_configuration_names_are_protected(project, out):
    service, request, _ = project
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError)):
        execute(service, **request, out=out)
    assert service.export({}).graph == before


def test_oversized_existing_destination_is_rejected_before_execution(project):
    service, request, root = project
    with (root / "oversized.json").open("wb") as stream:
        stream.truncate(25 * 1024 * 1024 + 1)
    before = service.export({}).graph
    with pytest.raises(ValueError, match="limit"):
        execute(service, **request, out="oversized.json")
    assert service.export({}).graph == before


def test_oversized_program_output_does_not_create_working_parent(project):
    service, request, root = project
    (root / "sum.py").write_text(
        'import sys\nwith open(sys.argv[2],"wb") as f:\n f.truncate(25*1024*1024+1)\n'
    )
    failed = execute(service, **request, out="large/result.json")
    assert failed["data"]["status"] == "failed"
    assert "limit" in failed["data"]["failure_detail"]
    assert not (root / "large").exists()


def test_output_growth_after_size_check_is_still_bounded(project, monkeypatch):
    from pathlib import Path

    service, request, root = project
    stat = Path.stat
    grown = False

    class GrowingStat:
        def __init__(self, path, info):
            self.path, self.info = path, info

        def __getattr__(self, key):
            return getattr(self.info, key)

        @property
        def st_size(self):
            nonlocal grown
            if not grown:
                grown = True
                remaining = 25 * 1024 * 1024 + 1 - self.info.st_size
                with self.path.open("ab") as stream:
                    while remaining:
                        chunk = b" " * min(remaining, 1024 * 1024)
                        stream.write(chunk)
                        remaining -= len(chunk)
            return self.info.st_size

    def stat_then_grow(path, *args, **kwargs):
        info = stat(path, *args, **kwargs)
        if path.name == "output.json" and path.parent.name.startswith("xyle-execute-"):
            return GrowingStat(path, info)
        return info

    monkeypatch.setattr(Path, "stat", stat_then_grow)
    run = execute(service, **request, out="result.json")
    assert grown
    assert run["data"]["status"] == "failed"
    assert "limit" in run["data"]["failure_detail"]
    assert not (root / "result.json").exists()


@pytest.mark.parametrize(
    "out", [".LiNeAgE/output.json", ".CoDeX/output.json", ".ENV.local", "agents.MD"]
)
def test_reserved_output_names_are_case_insensitive_for_portability(project, out):
    service, request, _ = project
    before = service.export({}).graph
    with pytest.raises((ValueError, ServiceError), match="protected"):
        execute(service, **request, out=out)
    assert service.export({}).graph == before
