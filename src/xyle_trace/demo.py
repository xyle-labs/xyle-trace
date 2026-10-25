"""Synthetic onboarding workflow shipped in the installed distribution."""

import json
from contextlib import closing
from pathlib import Path

from xyle_trace.contracts.validator import RunBlockedError
from xyle_trace.core.execution import execute
from xyle_trace.service import Service
from xyle_trace.store.sqlite_store import Store

_PROGRAM = """\
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
total = sum(
    row["record"]["data"]["value"]
    for item in payload["inputs"].values() for row in item["records"]
)
Path(sys.argv[2]).write_text(json.dumps({"total": total}))
"""

_POLICY = """\
- dataset: inputs
  require:
    every_row: [evidence_backed]
    evidence: exact_locator
  blocks: [analysis.*]
"""


def run_demo(root: Path, project: str) -> dict:
    """Populate a newly scaffolded project and verify its retained historical run.

    Only the packaged synthetic Python program is executed. Current unresolved
    corrections stay blocked even though the original retained inputs can replay.
    """
    (root / "sum.py").write_text(_PROGRAM)
    (root / "source.txt").write_text("A: 10 count\nB: 20 count\n")
    policy = root / "contracts.yaml"
    policy.write_text(_POLICY)
    with closing(Store(root / ".lineage/graph.db", create=False)) as store:
        service = Service(store, project, root=root, contracts_path=policy)
        source = service.register(
            {
                "type": "source",
                "natural_key": "observations",
                "data": {"uri": "source.txt", "title": "Synthetic observations"},
            }
        )
        snapshot = service.snapshot(
            {
                "kind": "local",
                "source_id": source.node.node.id,
                "path": "source.txt",
            }
        )
        dataset = service.register(
            {
                "type": "dataset",
                "natural_key": "inputs",
                "records": [
                    {"key": "A", "data": {"value": 10}},
                    {"key": "B", "data": {"value": 20}},
                ],
            }
        )
        service.link(
            {
                "kind": "evidence",
                "natural_key": "observed-rows",
                "data": {
                    "snapshot_id": snapshot.snapshot.node.id,
                    "provisional": False,
                    "locator": {"kind": "range", "value": "Lines 1–2"},
                },
                "records": [
                    {
                        "dataset_id": row.record.dataset_id,
                        "record_key": row.record.key,
                        "expected_record_revision": row.revision,
                        "expected_resolution_revision": row.resolution_revision,
                    }
                    for row in dataset.records
                ],
            }
        )
        transformation = service.register(
            {
                "type": "transformation",
                "natural_key": "analysis.sum",
                "data": {
                    "description": "Sum the synthetic observations",
                    "code_reference": "sum.py",
                },
            }
        )
        arguments = {
            "transformation_id": transformation.node.node.id,
            "input_ids": [dataset.node.node.id],
            "script": "sum.py",
            "params": {},
        }
        first = execute(service, key="original", **arguments)
        if first["data"]["status"] != "succeeded":
            raise ValueError("synthetic calculation failed")
        row = dataset.records[0]
        service.decide(
            {
                "kind": "correction",
                "natural_key": "replacement-pending",
                "rationale": "Synthetic correction: replacement evidence is pending.",
                "changes": [
                    {
                        "dataset_id": row.record.dataset_id,
                        "key": row.record.key,
                        "data": {"value": 99},
                        "expected_revision": row.revision,
                    }
                ],
            }
        )
        try:
            execute(service, key="blocked-current", **arguments)
        except RunBlockedError:
            pass
        else:
            raise ValueError("unresolved correction did not block current computation")
        replay = execute(service, key="replay-original", replay_of=first["id"])
        original_output = json.loads((root / first["data"]["output"]["path"]).read_text())
        replay_output = json.loads((root / replay["data"]["output"]["path"]).read_text())
        if not replay["data"].get("replay_verified") or replay_output != original_output:
            raise ValueError("historical replay did not verify")
        service.export({"format": "html", "destination": "lineage.html"})
        service.export({"destination": "lineage.json"})
        report = {
            "project": project,
            "original_total": original_output["total"],
            "correction_blocked": True,
            "replay_verified": True,
            "source_id": source.node.node.id,
            "snapshot_id": snapshot.snapshot.node.id,
            "dataset_id": dataset.node.node.id,
            "original": first,
            "replay": replay,
            "current_validation": service.validate({}).model_dump(mode="json"),
            "explorer": "lineage.html",
        }
        (root / "runs.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
