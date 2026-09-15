import fnmatch
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from xyle_trace.contracts.model import Contract
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.locators import verify_anchor
from xyle_trace.core.snapshots import CaptureError, read_snapshot
from xyle_trace.models.entities import EdgeType, Node, NodeType
from xyle_trace.models.provenance import DecisionData, EvidenceData, SnapshotData
from xyle_trace.store.sqlite_store import Store

# Node type each resolution status must point at. A resolution whose target
# node does not have this type does not actually back the record it claims to.
REQUIRED_TARGET_TYPE: dict[str, NodeType] = {
    "evidence_backed": NodeType.EVIDENCE_LINK,
    "assumption_backed": NodeType.DECISION,
}


class Violation(BaseModel):
    dataset: str
    kind: str
    record_key: str
    detail: str
    severity: Literal["block", "warn"] = "block"
    blocked_patterns: list[str] | None = None


def validate_contracts(
    store: Store,
    project_id: str,
    contracts: list[Contract],
    *,
    snapshot_root: Path | None = None,
    sweeps: bool = True,
) -> list[Violation]:
    """Evaluate every contract, plus (by default) the graph-wide sweeps.

    `sweeps` controls the two whole-project checks — `ungoverned_dataset` and
    `orphan_artifact` — that are properties of the graph, not of any one
    contract. Pass `sweeps=False` when `contracts` has already been filtered
    to the ones applicable to a single run: evaluating "nothing governs this"
    against a filtered policy set would generate false positives for datasets
    that a contract outside the filter does govern.
    """
    violations = []
    for contract in contracts:
        issues = _contract_violations(store, project_id, [contract], snapshot_root=snapshot_root)
        for issue in issues:
            issue.severity = contract.severity
            issue.blocked_patterns = sorted(set(contract.blocks))
        violations.extend(issues)
    if sweeps:
        violations.extend(_ungoverned_datasets(store, project_id, contracts))
        violations.extend(_orphan_artifacts(store, project_id))
    return violations


def _orphan_artifacts(store: Store, project_id: str) -> list[Violation]:
    """Report artifacts that nothing claims to have produced.

    An artifact can have incoming edges that are not about production at all:
    `used_input` records that a run *consumed* it, and `appears_in` records
    that a claim *appears in* it — neither says what made the file. Only a
    `produced` edge answers "what made this file", so that is the only edge
    type that clears an artifact here. A hand-made report with only an
    `appears_in` edge from a claim is exactly the case this check exists to
    catch.
    """
    violations = []
    for node in store.nodes_of_type(project_id, NodeType.ARTIFACT):
        if store.edges_to(project_id, node.id, types=[EdgeType.PRODUCED]):
            continue
        violations.append(
            Violation(
                dataset=node.natural_key,
                kind="orphan_artifact",
                record_key="",
                detail="artifact has no incoming edge; nothing records what produced it",
                severity="warn",
                blocked_patterns=[],
            )
        )
    return violations


def _ungoverned_datasets(
    store: Store, project_id: str, contracts: list[Contract]
) -> list[Violation]:
    """Report datasets holding records that no contract governs.

    Silence here is the dangerous default: a dataset nobody wrote a policy for
    still feeds claims, and a validator that walks only contracts will never
    mention it.
    """
    governed = set()
    for contract in contracts:
        try:
            node = store.dataset_node(project_id, contract.dataset)
        except ValueError:
            continue  # Ambiguity is reported by the contract's own evaluation.
        if node is not None:
            governed.add(node.id)
            governed.add(node.natural_key)
    violations = []
    for dataset_id in store.dataset_ids(project_id):
        if dataset_id in governed:
            continue
        node = store.get_node(project_id, dataset_id)
        name = node.natural_key if node else dataset_id
        if name in governed:
            continue
        violations.append(
            Violation(
                dataset=name,
                kind="ungoverned_dataset",
                record_key="",
                detail="dataset holds records but no contract governs it",
                severity="warn",
                blocked_patterns=[],
            )
        )
    return violations


def _contract_violations(
    store: Store,
    project_id: str,
    contracts: list[Contract],
    *,
    snapshot_root: Path | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    stored_datasets = set(store.dataset_ids(project_id))
    for contract in contracts:
        allowed = set(contract.every_row)
        try:
            dataset = store.dataset_node(project_id, contract.dataset)
        except ValueError as e:
            violations.append(
                Violation(
                    dataset=contract.dataset, kind="ambiguous_dataset", record_key="", detail=str(e)
                )
            )
            continue
        if dataset and dataset.natural_key != dataset.id and dataset.natural_key in stored_datasets:
            violations.append(
                Violation(
                    dataset=contract.dataset,
                    kind="legacy_dataset_reference",
                    record_key="",
                    detail="legacy records use a natural key; export and reimport under the dataset ID",
                )
            )
            continue
        resolutions = store.resolutions(project_id, dataset.id, resolve=False) if dataset else {}
        records = store.records(project_id, dataset.id, resolve=False) if dataset else []
        if not records:
            violations.append(
                Violation(
                    dataset=contract.dataset,
                    kind="dataset_not_found",
                    record_key="",
                    detail=f"no records found for dataset {contract.dataset}",
                )
            )
            continue
        for record in records:
            resolution = resolutions.get(record.key)
            if resolution is None or resolution.status not in allowed:
                violations.append(
                    Violation(
                        dataset=contract.dataset,
                        kind="unresolved_record",
                        record_key=record.key,
                        detail=(
                            "no resolution"
                            if resolution is None
                            else f"status {resolution.status} not permitted by contract"
                        ),
                    )
                )
                continue

            target = store.get_node(project_id, resolution.target_id)
            if target is None:
                violations.append(
                    Violation(
                        dataset=contract.dataset,
                        kind="missing_target",
                        record_key=record.key,
                        detail=f"resolution points at unknown node {resolution.target_id}",
                    )
                )
                continue

            required_type = REQUIRED_TARGET_TYPE.get(resolution.status)
            if required_type is None or target.type != required_type:
                violations.append(
                    Violation(
                        dataset=contract.dataset,
                        kind="target_type_mismatch",
                        record_key=record.key,
                        detail=(
                            f"status {resolution.status} requires target type "
                            f"{required_type.value if required_type else 'unknown'}, "
                            f"got {target.type.value}"
                        ),
                    )
                )
                continue

            issue = backing_issue(
                store,
                project_id,
                target,
                contract.evidence == "exact_locator",
                snapshot_root=snapshot_root,
                needs_anchor=contract.evidence == "verified_locator",
            )
            if issue:
                kind, detail = issue
                violations.append(
                    Violation(
                        dataset=contract.dataset, kind=kind, record_key=record.key, detail=detail
                    )
                )
    return violations


def backing_issue(
    store: Store,
    project_id: str,
    target: Node,
    needs_locator: bool,
    *,
    snapshot_root: Path | None = None,
    needs_anchor: bool = False,
) -> tuple[str, str] | None:
    """Check a backing node against the shared evidence/assumption requirements."""
    try:
        if target.type == NodeType.DECISION:
            if target.data.get("kind", "assumption") != "assumption":
                return "invalid_backing", "only assumption decisions can back records"
            metadata = DecisionData.model_validate(target.data)
        else:
            metadata = EvidenceData.model_validate(target.data)
    except ValidationError as e:
        return "invalid_backing", str(e)
    if metadata.provisional:
        return (
            "provisional_backing",
            "provisional evidence or assumptions cannot satisfy a contract",
        )
    if target.type == NodeType.DECISION:
        return None
    if needs_locator and metadata.locator is None:
        return "evidence_without_locator", "evidence requires a structured exact locator"
    snapshot = store.get_node(project_id, metadata.snapshot_id)
    if snapshot is None or snapshot.type != NodeType.SOURCE_SNAPSHOT:
        return "invalid_snapshot", "evidence must reference a source snapshot in this project"
    try:
        snapshot_data = SnapshotData.model_validate(snapshot.data)
    except ValidationError as e:
        return "invalid_snapshot", str(e)
    if needs_anchor:
        issue = _anchor_issue(metadata.locator, snapshot_data, snapshot_root)
        if issue:
            return issue
    source = store.get_node(project_id, snapshot_data.source_id)
    if source is None or source.type != NodeType.SOURCE:
        return "invalid_source", "snapshot must reference a source in this project"
    return None


def _anchor_issue(locator, snapshot_data, snapshot_root: Path | None) -> tuple[str, str] | None:
    if locator is None or locator.anchor is None:
        return (
            "evidence_without_anchor",
            "verified_locator requires a locator anchor that occurs in the captured bytes",
        )
    if snapshot_root is None:
        return "locator_unverifiable", "no snapshot root configured; cannot read captured bytes"
    try:
        data = read_snapshot(snapshot_root, snapshot_data.content_hash)
    except (OSError, CaptureError):
        return "locator_unverifiable", "captured bytes unavailable or unsafe"
    # The filename only names which bytes are *claimed*: re-hash what was actually
    # read before trusting it as the recorded snapshot. Provenance is proven, not
    # asserted by a path that happens to match.
    if hash_bytes(data) != snapshot_data.content_hash:
        return (
            "locator_snapshot_mismatch",
            "retained bytes do not match their recorded content hash",
        )
    result = verify_anchor(data, locator.anchor)
    if result is None:
        return (
            "locator_unverifiable",
            (
                "captured bytes are not decodable as text; capture the extracted text "
                "as its own snapshot and anchor into that"
            ),
        )
    if result is False:
        return (
            "locator_not_found",
            f"anchor {locator.anchor!r} does not occur in the captured bytes",
        )
    return None


def blocked_runs(contracts: list[Contract], violations: list[Violation]) -> set[str]:
    # Generated violations retain their own policy's patterns. Two contracts may
    # name the same dataset but impose different requirements on different runs.
    # Keep the conservative dataset fallback for older/caller-created violations.
    # A warn-severity violation is reported but never blocks a run.
    blocking = [v for v in violations if v.severity == "block"]
    failing = {v.dataset for v in blocking if v.blocked_patterns is None}
    return {pattern for v in blocking for pattern in v.blocked_patterns or []} | {
        pattern
        for contract in contracts
        if contract.dataset in failing and contract.severity == "block"
        for pattern in contract.blocks
    }


def is_blocked(run_name: str, blocked: set[str]) -> bool:
    return any(fnmatch.fnmatchcase(run_name, pattern) for pattern in blocked)


class RunBlockedError(RuntimeError):
    def __init__(self, run_name: str, violations: list[Violation]):
        self.violations = violations
        super().__init__(f"run {run_name!r} blocked by {len(violations)} contract violation(s)")


T = TypeVar("T")


def run_guarded(
    store: Store,
    project_id: str,
    run_name: str,
    contracts: list[Contract],
    operation: Callable[[], T],
    *,
    snapshot_root: Path | None = None,
) -> T:
    """Validate matching contracts immediately before invoking a computation.

    This preflight does not record a Run or isolate the operation from concurrent
    edits. Reproducible runners must consume immutable input versions.
    """
    applicable = [c for c in contracts if is_blocked(run_name, set(c.blocks))]
    # sweeps=False: a preflight against a filtered contract set cannot honestly
    # evaluate project-wide governance (ungoverned datasets, orphan artifacts) —
    # datasets governed by a contract outside `applicable` would look ungoverned.
    violations = validate_contracts(
        store, project_id, applicable, snapshot_root=snapshot_root, sweeps=False
    )
    blocking = [v for v in violations if v.severity == "block"]
    if blocking:
        raise RunBlockedError(run_name, blocking)
    return operation()
