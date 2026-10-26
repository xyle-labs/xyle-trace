"""Run against an installed wheel, from outside the source checkout."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path


def command(*args, input=None, expected=0):
    result = subprocess.run(
        ["xyle-trace", *args],
        input=input,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout


def workflow(directory):
    directory = Path(directory).resolve()
    root = Path(directory) / "demo-🌱"
    report = json.loads(command("demo", "--path", str(root), "--host", "both"))
    assert report["original_total"] == 30
    assert report["correction_blocked"] and report["replay_verified"]
    assert not report["current_validation"]["valid"]
    assert (root / "lineage.html").is_file()
    assert len(list(root.glob(".agents/skills/*/SKILL.md"))) == 5
    assert len(list(root.glob(".claude/skills/*/SKILL.md"))) == 5
    tomllib.loads((root / ".codex/config.toml").read_text())
    snapshot = json.loads(
        command(
            "show",
            "--project",
            "demo",
            "--db",
            str(root / ".lineage/graph.db"),
            "--root",
            str(root),
            "--snapshot",
            report["snapshot_id"],
            "--text",
        )
    )
    assert snapshot["verified"] and snapshot["text"] == "A: 10 count\nB: 20 count\n"
    result = json.loads(
        command(
            "record",
            "--project",
            "demo",
            "--db",
            str(root / ".lineage/graph.db"),
            "--root",
            str(root),
            "--contracts",
            str(root / "contracts.yaml"),
            "--operation",
            "register",
            "--request",
            "-",
            input=json.dumps(
                {
                    "type": "source",
                    "natural_key": "extra",
                    "data": {"uri": "extra.txt"},
                }
            ),
        )
    )
    assert result["node"]["node"]["natural_key"] == "extra"
    common = [
        "--project",
        "demo",
        "--db",
        str(root / ".lineage/graph.db"),
        "--root",
        str(root),
    ]
    health = json.loads(
        command(
            "doctor",
            *common,
            "--contracts",
            str(root / "contracts.yaml"),
            expected=1,
        )
    )
    assert health["policy"]["valid"] is False
    assert health["integrity"]["ok"] is True
    assert health["runs"]["replay_verified"] == 1
    assert health["capture"]["unresolved_records"]
    (root / "health.json").write_text(json.dumps(health, indent=2) + "\n")

    graph = json.loads((root / "lineage.json").read_text())
    artifact_id = next(
        edge["dst"]
        for edge in graph["edges"]
        if edge["type"] == "produced" and edge["src"] == report["original"]["id"]
    )
    selected = ["--node", artifact_id, "--run", report["original"]["id"], "--selector", "/total"]
    exact = json.loads(command("explain", *common, *selected))["explanation"]
    assert exact["selection"]["value"] == 30
    assert exact["selection"]["historical"] is True
    assert exact["inputs"][0]["records"][0]["record"]["data"] == {"value": 10}
    assert all(item["basis"] == "retained" for item in exact["inputs"])
    assert any(gap["kind"] == "current_inputs_differ" for gap in exact["gaps"])
    pinned = json.loads(
        command(
            "explain",
            *common,
            *selected,
            "--revision",
            exact["selection"]["revision"],
        )
    )["explanation"]
    assert pinned["selection"] == exact["selection"]
    (root / "historical-explanation.json").write_text(json.dumps(exact, indent=2) + "\n")

    delivered = subprocess.run(
        ["xyle-trace-run", "replay", *common, "--contracts", str(root / "contracts.yaml"),
         "--key", "delivered-replay", "--run", report["original"]["id"], "--out", "result.json"],
        text=True, capture_output=True, check=False,
    )
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    working_run = json.loads(delivered.stdout)
    working = working_run["data"]["working_output"]
    assert json.loads((root / "result.json").read_bytes()) == {"total": 30}
    assert working["content_hash"] == (
        "sha256:" + hashlib.sha256((root / "result.json").read_bytes()).hexdigest()
    )
    working_explanation = json.loads(command(
        "explain", *common, "--node", working["node_id"], "--selector", "/total",
    ))["explanation"]
    assert working_explanation["selection"]["value"] == 30
    assert working_explanation["selection"]["value_available"] is True
    assert working_explanation["selection"]["run_id"] == working_run["id"]
    delivered_health = json.loads(command(
        "doctor", *common, "--contracts", str(root / "contracts.yaml"), expected=1,
    ))
    assert delivered_health["integrity"]["ok"] is True
    assert working["node_id"] not in delivered_health["capture"]["orphan_artifact_ids"]

    report_options = ["export", *common, "--format", "report", "--node", artifact_id]
    preview = json.loads(command(*report_options, "--preview"))
    assert preview["path"] is None
    assert preview["graph"]["explanations"][artifact_id]["selection"]["value"] == {
        "total": 30,
    }
    assert not (root / "report.html").exists()
    written = json.loads(command(*report_options, "--out", "report.html"))
    report_hash = "sha256:" + hashlib.sha256((root / "report.html").read_bytes()).hexdigest()
    assert written["content_hash"] == preview["content_hash"] == report_hash
    assert written["graph"] is None

    bundle_options = [
        "export",
        *common,
        "--format",
        "bundle",
        "--contracts",
        str(root / "contracts.yaml"),
    ]
    metadata = json.loads(command(*bundle_options, "--out", "metadata.zip"))
    with zipfile.ZipFile(root / metadata["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["include_content"] is False and manifest["content_complete"] is False
        assert not any(name.startswith("blobs/") for name in archive.namelist())
    complete = json.loads(
        command(
            *bundle_options,
            "--out",
            "handoff.zip",
            "--include-content",
        )
    )
    with zipfile.ZipFile(root / complete["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["include_content"] is True and manifest["content_complete"] is True
        assert any(name.startswith("blobs/") for name in archive.namelist())
    restored_root = Path(directory) / "restored"
    restored = json.loads(
        command(
            "restore",
            "--bundle",
            str(root / complete["path"]),
            "--path",
            str(restored_root),
        )
    )
    assert restored["content_complete"] is True and restored["policy_included"] is True
    assert not (restored_root / "sum.py").exists()
    assert not (restored_root / "source.txt").exists()
    assert not (restored_root / "result.json").exists()
    assert not list(restored_root.rglob("*.py"))
    restored_common = [
        "--project",
        "demo",
        "--db",
        restored["database_path"],
        "--root",
        str(restored_root),
    ]
    restored_explanation = json.loads(
        command(
            "explain",
            *restored_common,
            *selected,
        )
    )["explanation"]
    assert restored_explanation["selection"] == exact["selection"]
    assert restored_explanation["inputs"] == exact["inputs"]
    assert restored_explanation["calculations"] == exact["calculations"]
    restored_working = json.loads(command(
        "explain", *restored_common, "--node", working["node_id"], "--selector", "/total",
    ))["explanation"]
    assert restored_working["selection"] == working_explanation["selection"]
    (restored_root / "historical-explanation.json").write_text(
        json.dumps(restored_explanation, indent=2) + "\n"
    )
    restored_snapshot = json.loads(
        command(
            "show",
            *restored_common,
            "--snapshot",
            report["snapshot_id"],
            "--text",
        )
    )
    assert restored_snapshot["verified"] and restored_snapshot["text"] == snapshot["text"]
    original_graph = root / "current-graph.json"
    restored_graph = restored_root / "current-graph.json"
    command("export", *common, "--out", str(original_graph))
    command("export", *restored_common, "--out", str(restored_graph))
    assert json.loads(original_graph.read_text()) == json.loads(restored_graph.read_text())
    from xyle_trace.mcp.__main__ import _parser, _resolve

    moved = root.with_name("moved")
    root.rename(moved)
    assert _resolve(_parser().parse_args([]), moved, {}) == (
        "demo",
        moved / ".lineage/graph.db",
        moved,
        moved / "contracts.yaml",
    )
    summary = {
        "original_project": str(moved),
        "restored_project": str(restored_root),
        "artifact_id": artifact_id,
        "original_run_id": report["original"]["id"],
        "report_content_hash": report_hash,
        "bundle": str(moved / complete["path"]),
        "bundle_content_hash": complete["content_hash"],
        "retained_value": exact["selection"]["value"],
        "doctor_exit_code": 1,
        "doctor_integrity_ok": health["integrity"]["ok"],
        "working_artifact_id": working["node_id"],
        "working_content_hash": working["content_hash"],
    }
    (Path(directory) / "smoke-summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    import xyle_trace

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="retain synthetic smoke artifacts in a new directory"
    )
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[2]
    assert not Path(xyle_trace.__file__).resolve().is_relative_to(checkout / "src")
    if importlib.util.find_spec("mcp") is None:
        optional = subprocess.run(["xyle-trace-mcp"], text=True, capture_output=True, check=False)
        assert optional.returncode == 2 and not optional.stdout
        assert "git+https://github.com/xyle-labs/xyle-trace.git" in optional.stderr
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        workflow(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="xyle-trace-wheel-") as directory:
            workflow(Path(directory))
    print(
        "Installed wheel capture, doctor, exact result, report hash, bundle restore and history passed."
    )


if __name__ == "__main__":
    main()
