"""Both configuration paths for the stdio entry point: explicit flags and discovery.

A globally installed plugin cannot pass ``--project``/``--db``/``--root``/``--contracts``,
so when they are absent the server discovers them from the working directory: an
optional ``./.lineage/config.json``, environment-variable overrides, and the
conventional layout (``./.lineage/graph.db``, ``./contracts.yaml``, root ``.``) for
everything except the project id, which has no convention and must come from
somewhere explicit.
"""

import json

import pytest

pytest.importorskip("mcp.client")

from xyle_trace.mcp.__main__ import DiscoveryError, _parser, _resolve, main
from xyle_trace.mcp.server import ServerConfig
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.store.sqlite_store import Store


def _make_project(root):
    """A minimal initialized project at the conventional layout under ``root``."""
    db = root / ".lineage" / "graph.db"
    store = Store(db)
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    store.close()
    (root / "contracts.yaml").write_text("[]\n")
    return db


def test_explicit_flags_are_used_unchanged_regardless_of_cwd(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db = _make_project(project_dir)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()

    args = _parser().parse_args(
        [
            "--project",
            "p",
            "--db",
            str(db),
            "--root",
            str(project_dir),
            "--contracts",
            str(project_dir / "contracts.yaml"),
        ]
    )
    project, db_, root_, contracts = _resolve(args, other_cwd, {})
    assert (project, db_, root_, contracts) == (
        "p",
        db,
        project_dir,
        project_dir / "contracts.yaml",
    )
    ServerConfig(project, db_, root_, contracts)


def test_discovery_reads_config_json_and_the_conventional_layout(tmp_path):
    _make_project(tmp_path)
    (tmp_path / ".lineage" / "config.json").write_text(json.dumps({"project": "p"}))

    args = _parser().parse_args([])
    project, db_, root_, contracts = _resolve(args, tmp_path, {})

    assert project == "p"
    assert db_ == tmp_path / ".lineage" / "graph.db"
    assert root_ == tmp_path
    assert contracts == tmp_path / "contracts.yaml"
    ServerConfig(project, db_, root_, contracts)


def test_discovery_honors_environment_variable_override_for_project(tmp_path):
    _make_project(tmp_path)

    args = _parser().parse_args([])
    project, db_, root_, contracts = _resolve(args, tmp_path, {"XYLE_TRACE_PROJECT": "p"})

    assert project == "p"
    ServerConfig(project, db_, root_, contracts)


def test_partial_flags_fill_the_rest_from_discovery(tmp_path):
    _make_project(tmp_path)

    args = _parser().parse_args(["--project", "p"])
    project, db_, _root, _contracts = _resolve(args, tmp_path, {})

    assert project == "p"
    assert db_ == tmp_path / ".lineage" / "graph.db"


def test_discovery_without_a_determinable_project_fails_clearly(tmp_path):
    args = _parser().parse_args([])
    with pytest.raises(DiscoveryError, match="project"):
        _resolve(args, tmp_path, {})


def test_malformed_config_json_fails_clearly(tmp_path):
    (tmp_path / ".lineage").mkdir()
    (tmp_path / ".lineage" / "config.json").write_text("not json")
    args = _parser().parse_args([])
    with pytest.raises(DiscoveryError, match="config.json"):
        _resolve(args, tmp_path, {})


def test_main_reports_an_actionable_error_and_does_not_start_a_broken_server(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    code = main([])
    assert code == 2
    captured = capsys.readouterr()
    assert "project" in captured.err.lower()
