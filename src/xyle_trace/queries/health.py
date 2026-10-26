"""Read-only project health: policy, retained integrity, freshness and review gaps."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import backing_issue, validate_contracts
from xyle_trace.core.handoff import (
    _check_reference,
    hash_project_file,
    read_project_file,
    retained_references,
)
from xyle_trace.models.entities import EdgeType, NodeType
from xyle_trace.models.provenance import EvidenceData, SnapshotData
from xyle_trace.store.sqlite_store import Store


def _file_hashes_valid(value):
    return isinstance(value, dict) and all(
        item is None or (isinstance(item, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", item))
        for item in value.values()
    )


def _capture_gaps_valid(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _working_output_consistent(run, nodes, edges):
    descriptor = run.data.get("working_output")
    if descriptor is None:
        return True
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("node_id"), str):
        return False
    artifact = nodes.get(descriptor["node_id"])
    output = run.data.get("output")
    hashes = run.data.get("output_file_hashes")
    return (
        run.data.get("capture_mode") == "managed"
        and artifact is not None
        and artifact.type == NodeType.ARTIFACT
        and isinstance(descriptor.get("path"), str)
        and artifact.data.get("path") == descriptor["path"]
        and isinstance(output, dict)
        and isinstance(descriptor.get("content_hash"), str)
        and descriptor["content_hash"] == output.get("content_hash")
        and _file_hashes_valid(hashes)
        and hashes.get(artifact.id) == descriptor["content_hash"]
        and any(
            edge.type == EdgeType.PRODUCED
            and edge.src == run.id
            and edge.dst == artifact.id
            and not edge.data.get("provisional", False)
            for edge in edges
        )
    )


def _graph_issues(store, project_id, nodes, edges):
    """Check stored relationships independently of policy coverage and retained bytes."""
    issues = []
    by_id = {node.id: node for node in nodes}
    for edge in edges:
        for field in ("src", "dst"):
            target = getattr(edge, field)
            if target not in by_id:
                issues.append(
                    {
                        "kind": "dangling_edge",
                        "edge_id": edge.id,
                        "field": field,
                        "target_id": target,
                    }
                )
    references = {
        NodeType.SOURCE_SNAPSHOT: ("source_id", NodeType.SOURCE, SnapshotData),
        NodeType.EVIDENCE_LINK: ("snapshot_id", NodeType.SOURCE_SNAPSHOT, EvidenceData),
    }
    for node in nodes:
        if node.type == NodeType.RUN:
            if not _working_output_consistent(node, by_id, edges):
                issues.append({"kind": "inconsistent_working_output", "node_id": node.id})
            for field, valid in (
                ("capture_gaps", _capture_gaps_valid),
                ("input_file_hashes", _file_hashes_valid),
                ("output_file_hashes", _file_hashes_valid),
            ):
                if field in node.data and not valid(node.data[field]):
                    issues.append(
                        {"kind": "invalid_run_metadata", "node_id": node.id, "field": field}
                    )
            if node.data.get("capture_mode") == "managed":
                required = ["bundle"]
                if node.data.get("status") == "succeeded":
                    required.append("output")
                for field in required:
                    if node.data.get(field) is None:
                        issues.append(
                            {
                                "kind": "missing_retained_reference",
                                "node_id": node.id,
                                "field": field,
                            }
                        )
        if node.type not in references:
            continue
        field, expected, model = references[node.type]
        raw = node.data.get(field)
        target_id = raw.strip() if isinstance(raw, str) else None
        target = by_id.get(target_id)
        issue = {"node_id": node.id, "field": field, "target_id": target_id}
        if not target_id:
            issues.append({**issue, "kind": "invalid_reference"})
        elif target is None:
            issues.append({**issue, "kind": "missing_reference"})
        elif target.type != expected:
            issues.append({**issue, "kind": "reference_type_mismatch"})
        try:
            model.model_validate(node.data)
        except ValidationError:
            # Report the affected node without copying source metadata into diagnostics.
            issues.append({"kind": "invalid_provenance_metadata", "node_id": node.id})
    dataset_ids = set(store.dataset_ids(project_id))
    # Orphan resolutions may have neither a remaining record nor a registered dataset.
    # Store has no public iterator over those dataset IDs; this query remains read-only.
    dataset_ids.update(
        row[0]
        for row in store._conn.execute(
            "SELECT DISTINCT dataset_id FROM resolutions WHERE project_id = ?", (project_id,)
        )
    )
    for dataset_id in sorted(dataset_ids):
        dataset = by_id.get(dataset_id)
        if dataset is None or dataset.type != NodeType.DATASET:
            issues.append({"kind": "invalid_record_dataset", "dataset_id": dataset_id})
        keys = {record.key for record in store.records(project_id, dataset_id, resolve=False)}
        for key, resolution in store.resolutions(project_id, dataset_id, resolve=False).items():
            identity = {
                "dataset_id": dataset_id,
                "record_key": key,
                "target_id": resolution.target_id,
            }
            if key not in keys:
                issues.append({**identity, "kind": "orphan_resolution"})
            target = by_id.get(resolution.target_id)
            expected = (
                NodeType.DECISION
                if resolution.status == "assumption_backed"
                else NodeType.EVIDENCE_LINK
            )
            if target is None:
                issues.append({**identity, "kind": "missing_resolution_target"})
            elif target.type != expected:
                issues.append({**identity, "kind": "resolution_target_type_mismatch"})
            elif problem := backing_issue(store, project_id, target, False):
                issues.append(
                    {**identity, "kind": "invalid_resolution_backing", "reason": problem[0]}
                )
    return issues


def _run_time(run):
    try:
        timestamp = datetime.fromisoformat(run.data.get("finished_at"))
    except (ValueError, TypeError):
        timestamp = run.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC), run.id


def _file_status(root, path, expected=None):
    result = {"path": path, "expected_hash": expected, "actual_hash": None}
    if root is None:
        return {**result, "status": "root_unavailable"}
    if not isinstance(path, str) or not path:
        return {**result, "status": "path_unknown"}
    try:
        actual = hash_project_file(root, path)
    except FileNotFoundError:
        return {**result, "status": "missing"}
    except ValueError as error:
        return {**result, "status": "oversize" if "limit" in str(error) else "unsafe"}
    except OSError:
        return {**result, "status": "unreadable"}
    return {
        **result,
        "actual_hash": actual,
        "status": "unobserved"
        if expected is None
        else "current"
        if actual == expected
        else "changed",
    }


def _receipt(root, path, nodes, project_id):
    result = {"path": path}
    if root is None:
        return {**result, "status": "root_unavailable"}
    try:
        receipt = json.loads(read_project_file(root, path))
        if not isinstance(receipt, dict):
            raise TypeError("invalid receipt")
        nested = receipt.get("run")
        identity = nested if isinstance(nested, dict) else receipt
        run_id = identity.get("run_id", identity.get("node_id", identity.get("id")))
        if not isinstance(run_id, str):
            raise TypeError("receipt has no run ID")
        if identity.get("project_id", receipt.get("project_id", project_id)) != project_id:
            return {**result, "run_id": run_id, "status": "project_mismatch"}
        node = nodes.get(run_id)
        return {
            **result,
            "run_id": run_id,
            "status": "run_missing_locally"
            if node is None
            else "invalid_run_reference"
            if node.type != NodeType.RUN
            else "present_locally",
        }
    except FileNotFoundError:
        return {**result, "status": "missing"}
    except (OSError, ValueError, TypeError, RecursionError):
        return {**result, "status": "invalid_or_unreadable"}


def project_health(
    store: Store,
    project_id: str,
    root: Path | None,
    contracts: list[Contract],
    *,
    receipt_paths: list[str] | None = None,
) -> dict:
    """Observe only registered paths and explicit receipt paths; never mutate graph.

    File checks are bounded and describe one observation, not continuous monitoring.
    Policy validity is independent of content integrity and execution verification.
    """
    root = Path(root).resolve(strict=True) if root is not None else None
    with store.transaction(write=False):
        nodes = store.all_nodes(project_id)
        by_id = {node.id: node for node in nodes}
        violations = validate_contracts(
            store,
            project_id,
            contracts,
            snapshot_root=root / ".lineage" / "snapshots" if root else None,
        )
        edges = store.all_edges(project_id)
        graph_issues = _graph_issues(store, project_id, nodes, edges)
        unresolved = [
            {"dataset_id": dataset_id, "record_keys": keys}
            for dataset_id in store.dataset_ids(project_id)
            if (keys := store.unresolved_keys(project_id, dataset_id))
        ]
    runs = [node for node in nodes if node.type == NodeType.RUN]
    counts = {
        key: 0
        for key in (
            "total",
            "reported",
            "managed",
            "unknown",
            "replayed",
            "replay_verified",
            "failed",
            "started",
            "succeeded",
        )
    }
    gaps = []
    for run in runs:
        counts["total"] += 1
        mode = run.data.get("capture_mode")
        counts[mode if mode in ("reported", "managed") else "unknown"] += 1
        status = run.data.get("status")
        if status in ("failed", "started", "succeeded"):
            counts[status] += 1
        counts["replayed"] += bool(run.data.get("replay_of"))
        counts["replay_verified"] += run.data.get("replay_verified") is True
        captured_gaps = run.data.get("capture_gaps", [])
        missing = (
            list(captured_gaps)
            if _capture_gaps_valid(captured_gaps)
            else ["invalid_run_metadata:capture_gaps"]
        )
        if mode != "managed" and "execution_not_observed" not in missing:
            missing.append("execution_not_observed")
        if missing:
            gaps.append({"run_id": run.id, "gaps": missing})
    retained = []
    for ref in retained_references(nodes):
        item = {**ref, "status": "root_unavailable"}
        try:
            digest = _check_reference(ref)
            if root is not None:
                actual = hash_project_file(root, ref["path"])
                item["status"] = "verified" if actual == digest else "corrupt"
        except FileNotFoundError:
            item["status"] = "missing"
        except ValueError as error:
            item["status"] = "oversize" if "limit" in str(error) else "unsafe"
        except OSError:
            item["status"] = "unreadable"
        retained.append(item)
    artifacts, orphans, provisional = [], [], []
    for artifact in [node for node in nodes if node.type == NodeType.ARTIFACT]:
        production = [
            edge for edge in edges if edge.type == EdgeType.PRODUCED and edge.dst == artifact.id
        ]
        observed = [edge for edge in production if not edge.data.get("provisional", False)]
        if not production:
            orphans.append(artifact.id)
        elif not observed:
            provisional.append(artifact.id)
        candidates = [
            by_id[edge.src]
            for edge in observed
            if edge.src in by_id
            and by_id[edge.src].type == NodeType.RUN
            and by_id[edge.src].data.get("status") == "succeeded"
        ]
        candidates.sort(key=_run_time, reverse=True)
        latest = candidates[0] if candidates else None
        file_hashes = latest.data.get("output_file_hashes", {}) if latest else {}
        expected = file_hashes.get(artifact.id) if _file_hashes_valid(file_hashes) else None
        if (
            expected is None
            and latest
            and latest.data.get("capture_mode") == "managed"
            and isinstance(latest.data.get("output"), dict)
            and latest.data["output"].get("path") == artifact.data.get("path")
        ):
            expected = latest.data["output"].get("content_hash")
        working = latest.data.get("working_output") if latest else None
        if (
            isinstance(working, dict)
            and working.get("node_id") == artifact.id
            and artifact.data.get("content_hash") != expected
        ):
            # Older working-output receipts retain their original hashes. Only
            # the latest producer must match the current artifact metadata.
            graph_issues.append(
                {
                    "kind": "inconsistent_working_output",
                    "node_id": latest.id,
                    "artifact_id": artifact.id,
                }
            )
        artifacts.append(
            {
                "node_id": artifact.id,
                "run_id": latest.id if latest else None,
                **_file_status(root, artifact.data.get("path"), expected),
            }
        )
    pending = sorted(
        node.id
        for node in nodes
        if node.type == NodeType.CLAIM and node.data.get("review_status") == "needs_review"
    )
    blocking = [v.model_dump(mode="json") for v in violations if v.severity == "block"]
    verified = all(item["status"] == "verified" for item in retained) and all(
        item["status"] == "current" for item in artifacts
    )
    concrete_failures = {"missing", "corrupt", "unsafe", "changed"}
    failed = bool(graph_issues) or any(
        item["status"] in concrete_failures for item in [*retained, *artifacts]
    )
    integrity_status = "failed" if failed else "verified" if verified else "unknown"
    return {
        "project_id": project_id,
        "policy": {
            "valid": not blocking,
            "blocking": blocking,
            "violations": [v.model_dump(mode="json") for v in violations],
        },
        "integrity": {
            "ok": integrity_status == "verified",
            "status": integrity_status,
            "graph_issues": graph_issues,
            "retained": retained,
            "artifacts": artifacts,
        },
        "capture": {
            "gaps": gaps,
            "unresolved_records": unresolved,
            "orphan_artifact_ids": sorted(orphans),
            "provisional_production_artifact_ids": sorted(provisional),
        },
        "review": {"pending_claim_ids": pending},
        "runs": counts,
        "receipts": [_receipt(root, path, by_id, project_id) for path in receipt_paths or []],
    }
