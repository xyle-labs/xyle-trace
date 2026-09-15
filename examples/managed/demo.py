"""Run with a new output directory; preserve artifacts for inspection."""

import json
import shutil
import sys
from contextlib import closing
from pathlib import Path

from xyle_trace.cli.main import main as init
from xyle_trace.contracts.validator import RunBlockedError
from xyle_trace.core.execution import execute
from xyle_trace.service import Service
from xyle_trace.store.sqlite_store import Store


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__).with_name("sum.py"), root / "sum.py")
    (root / "source.txt").write_text("A: 10 count\nB: 20 count\n")
    policy = root / "contracts.yaml"
    policy.write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n    evidence: exact_locator\n  blocks: [analysis.*]\n"
    )
    db = root / ".lineage/graph.db"
    init(["init", "--project", "managed-demo", "--db", str(db)])
    with closing(Store(db)) as store:
        service = Service(store, "managed-demo", root=root, contracts_path=policy)
        source = service.register(
            {"type": "source", "natural_key": "observations", "data": {"uri": "source.txt"}}
        )
        snapshot = service.snapshot(
            {"kind": "local", "source_id": source.node.node.id, "path": "source.txt"}
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
                "data": {"description": "Sum the observed values", "code_reference": "sum.py"},
            }
        )
        arguments = {
            "transformation_id": transformation.node.node.id,
            "input_ids": [dataset.node.node.id],
            "script": "sum.py",
            "params": {},
        }
        first = execute(service, key="original", **arguments)
        row = dataset.records[0]
        service.decide(
            {
                "kind": "correction",
                "natural_key": "new-observation-pending",
                "rationale": "Demonstrate an unresolved correction; replacement evidence is pending.",
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
            execute(service, key="blocked", **arguments)
        except RunBlockedError:
            pass
        else:
            raise AssertionError("Current unresolved inputs must block new computation")
        replay = execute(service, key="replay-original", replay_of=first["id"])
        assert replay["data"]["replay_verified"]
        assert json.loads((root / replay["data"]["output"]["path"]).read_bytes()) == {"total": 30}
        service.export({"format": "html", "destination": "lineage.html"})
        service.export({"destination": "lineage.json"})
        report = {
            "original": first,
            "replay": replay,
            "current_validation": service.validate({}).model_dump(mode="json"),
        }
        (root / "runs.json").write_text(json.dumps(report, indent=2))
    print(f"Retained original total: 30. Verified replay. Explorer: {root / 'lineage.html'}")


if __name__ == "__main__":
    main()
