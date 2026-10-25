import json

from xyle_trace.cli.main import main
from xyle_trace.models.entities import NodeType
from xyle_trace.store.sqlite_store import Store


def test_cli_doctor_explain_report_and_restore(tmp_path, capsys):
    root = tmp_path / "demo"
    assert main(["demo", "--path", str(root), "--project", "p"]) == 0
    capsys.readouterr()
    db = root / ".lineage/graph.db"
    store = Store(db)
    artifact = store.nodes_of_type("p", NodeType.ARTIFACT)[0]
    store.close()
    common = ["--project", "p", "--db", str(db), "--root", str(root)]
    assert main(["doctor", *common, "--contracts", str(root / "contracts.yaml")]) == 1
    assert json.loads(capsys.readouterr().out)["review"] is not None
    assert main(["explain", *common, "--node", artifact.id, "--selector", "/total"]) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["explanation"]["selection"]["value"] == 30
    assert main(["export", *common, "--format", "report", "--node", artifact.id, "--preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["graph"]["explanations"][artifact.id]["selection"]["value"]["total"] == 30
    assert (
        main(
            ["export", *common, "--format", "report", "--node", artifact.id, "--out", "report.html"]
        )
        == 0
    )
    capsys.readouterr()
    assert (root / "report.html").is_file()
    assert (
        main(
            [
                "export",
                *common,
                "--format",
                "bundle",
                "--include-content",
                "--contracts",
                str(root / "contracts.yaml"),
                "--out",
                "handoff.zip",
            ]
        )
        == 0
    )
    capsys.readouterr()
    destination = tmp_path / "restored"
    assert main(["restore", "--bundle", str(root / "handoff.zip"), "--path", str(destination)]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["content_complete"] is True
    assert (destination / ".lineage/graph.db").is_file()
    assert not (destination / "sum.py").exists()


def test_cli_restore_refuses_existing_project(tmp_path, capsys):
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep")
    assert (
        main(["restore", "--bundle", str(tmp_path / "missing.zip"), "--path", str(destination)])
        == 2
    )
    assert "error" in json.loads(capsys.readouterr().out)
    assert (destination / "keep.txt").read_text() == "keep"
