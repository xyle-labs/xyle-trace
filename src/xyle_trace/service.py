"""Atomic, revision-checked application operations; independent of any transport."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from xyle_trace.contracts.loader import ContractError, parse_contracts
from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import (
    RunBlockedError,
    backing_issue,
    blocked_runs,
    is_blocked,
    run_guarded,
    validate_contracts,
)
from xyle_trace.core.hashing import hash_bytes, hash_file
from xyle_trace.core.ids import make_id
from xyle_trace.core.locators import decode_text
from xyle_trace.core.revisions import (
    RevisionConflictError,
    canonical_json,
    check_revision,
    require_revision,
    revision,
)
from xyle_trace.core.snapshots import (
    CaptureError,
    CaptureLimits,
    HTTPTransport,
    SnapshotCapture,
    public_http,
    read_snapshot,
)
from xyle_trace.exporters.html_graph import render_html
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models import operations as op
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.models.provenance import SnapshotData
from xyle_trace.queries import traversal
from xyle_trace.queries.traversal import _dependencies, impact
from xyle_trace.store.sqlite_store import Store

_REGISTER = TypeAdapter(op.RegisterRequest)
_LINK = TypeAdapter(op.LinkRequest)
_DECIDE = TypeAdapter(op.DecideRequest)
_SNAPSHOT = TypeAdapter(op.SnapshotRequest)
_QUERY = TypeAdapter(op.QueryRequest)
_VALIDATE = TypeAdapter(op.ValidateRequest)
_EXPORT = TypeAdapter(op.ExportRequest)
_RUN = TypeAdapter(op.RecordRunRequest)


class ServiceError(ValueError):
    def __init__(self, code: str, message: str, **details):
        self.error = op.ErrorDetail(code=code, message=message, details=details)
        super().__init__(message)


@dataclass
class _Changes:
    records: set[tuple[str, str]] = field(default_factory=set)
    claims: set[str] = field(default_factory=set)


class Service:
    def __init__(
        self,
        store: Store,
        project_id: str,
        *,
        contracts: list[Contract] | None = None,
        contracts_path: Path | None = None,
        root: Path | None = None,
        capture_limits: CaptureLimits | None = None,
        http_transport: HTTPTransport = public_http,
        validate_policies: bool = True,
    ):
        self.store = store
        self.project_id = project_id
        if (contracts is None) == (contracts_path is None):
            raise ServiceError("contract_error", "configure either contracts or a contracts_path")
        self.contracts_path = (
            Path(contracts_path).absolute() if contracts_path is not None else None
        )
        self.contracts = [contract.model_copy(deep=True) for contract in contracts or []]
        if validate_policies:
            self._policy_snapshot()
        self._node(make_id(project_id, "project", project_id), {NodeType.PROJECT})
        self._capture = (
            SnapshotCapture(root, limits=capture_limits, transport=http_transport)
            if root is not None
            else None
        )
        self.root = self._capture.root if self._capture else None

    @property
    def _snapshot_root(self) -> Path | None:
        """Where captured source bytes live, for verified_locator anchor checks.

        None when the service was opened without a capture root configured;
        validate_contracts/backing_issue treat that as honestly unverifiable
        rather than silently skipping the check.
        """
        return self.root / ".lineage" / "snapshots" if self.root else None

    def _policy_snapshot(self) -> tuple[list[Contract], str]:
        if self.contracts_path is None:
            return self.contracts, revision(
                {"contracts": [contract.model_dump(mode="json") for contract in self.contracts]}
            )
        try:
            data = self.contracts_path.read_bytes()
            contracts = parse_contracts(data, source=str(self.contracts_path))
        except (OSError, ContractError) as error:
            raise ServiceError(
                "contract_error", f"cannot load configured policies: {error}"
            ) from error
        self.contracts = contracts
        return contracts, hash_bytes(data)

    @contextmanager
    def _request(
        self, adapter: TypeAdapter, request, *, transaction: bool = True, read_only: bool = False
    ) -> Iterator:
        try:
            parsed = adapter.validate_python(request)
            if adapter in (_REGISTER, _LINK, _DECIDE):
                self._policy_snapshot()
            with self.store.transaction(write=not read_only) if transaction else nullcontext():
                yield parsed
        except ValidationError as e:
            errors = [
                {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
                for item in e.errors(include_input=False, include_url=False)
            ]
            raise ServiceError(
                "invalid_request", "operation schema validation failed", errors=errors
            ) from e

    def _record_states(self, dataset_id: str) -> list[op.RecordState]:
        resolutions = self.store.resolutions(self.project_id, dataset_id, resolve=False)
        return [
            op.RecordState(
                record=record,
                revision=revision(record.data),
                resolution=resolutions.get(record.key),
                resolution_revision=revision(self._resolution_data(resolutions[record.key]))
                if record.key in resolutions
                else None,
            )
            for record in self.store.records(self.project_id, dataset_id, resolve=False)
        ]

    def _run_state(self, node: Node) -> dict:
        state = {"node_revision": revision(node.data)}
        if node.type == NodeType.DATASET:
            self._dataset(node.id)  # Reject ambiguous or legacy input/output rows.
            state["records"] = [
                {
                    "key": row.record.key,
                    "revision": row.revision,
                    "resolution_revision": row.resolution_revision,
                }
                for row in self._record_states(node.id)
            ]
        return state

    def _run_file(self, node: Node, path: str | None, *, allow_missing: bool) -> str | None:
        if path is None:
            return None
        if self.root is None:
            raise ServiceError("capture_failed", "run file hashing requires a configured root")
        try:
            if not isinstance(path, str) or Path(path).is_absolute():
                raise ServiceError("invalid_reference", "run files require project-relative paths")
            resolved = (self.root / path).resolve()
            if not resolved.is_relative_to(self.root):
                raise ServiceError("invalid_reference", "run file path escapes the project root")
            if allow_missing and not resolved.exists():
                return None
            if not resolved.is_file():
                raise ServiceError(
                    "capture_failed", "declared run file is missing or not a regular file"
                )
            before = resolved.stat()
            content_hash = hash_file(resolved)
            after = resolved.stat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ServiceError(
                    "conflict", "run file changed while being hashed", node_id=node.id
                )
            if node.type == NodeType.SOURCE_SNAPSHOT and content_hash != node.data.get(
                "content_hash"
            ):
                raise ServiceError(
                    "capture_failed", "snapshot bytes do not match the registered hash"
                )
            return content_hash
        except (OSError, ValueError, RuntimeError) as error:
            if isinstance(error, ServiceError):
                raise
            raise ServiceError("capture_failed", "could not hash the declared run file") from error

    def _run_observations(
        self, nodes: list[Node], states: dict, paths: dict, *, allow_missing=False
    ):
        file_hashes = {
            node.id: self._run_file(node, paths[node.id], allow_missing=allow_missing)
            for node in nodes
        }
        fingerprints = {
            node.id: revision(
                {
                    "state": states[node.id],
                    "path": paths[node.id],
                    "file_hash": file_hashes[node.id],
                }
            )
            for node in nodes
        }
        return fingerprints, file_hashes

    def _check_run_states(self, states: dict):
        for node_id, state in states.items():
            self._check_revision(
                self._run_state(self._node(node_id)), revision(state), node_id=node_id
            )

    def _run_result(self, node: Node) -> op.RunResult:
        return op.RunResult(
            run=self._node_state(node),
            **{
                key: node.data[key]
                for key in (
                    "input_fingerprints",
                    "output_fingerprints",
                    "input_file_hashes",
                    "output_file_hashes",
                    "capture_gaps",
                    "policy_hash",
                )
            },
        )

    def record_run(self, request: op.RecordRunRequest | dict) -> op.RunResult:
        with self._request(_RUN, request, transaction=False) as parsed:
            if self.store.in_transaction:
                raise ServiceError(
                    "invalid_request", "run capture cannot run inside a write transaction"
                )
            try:
                if parsed.action == "start":
                    return self._start_run(parsed)
                return self._finish_run(parsed)
            except RunBlockedError as error:
                raise ServiceError(
                    "run_blocked",
                    str(error),
                    violations=[v.model_dump(mode="json") for v in error.violations],
                ) from error

    def _start_run(self, request: op.StartRunRequest) -> op.RunResult:
        if len(set(request.input_ids)) != len(request.input_ids):
            raise ServiceError("invalid_request", "duplicate run input IDs")
        contracts, policy_hash = self._policy_snapshot()
        with self.store.transaction(write=False):
            transformation = self._node(request.transformation_id, {NodeType.TRANSFORMATION})
            run_name = transformation.natural_key
            run_guarded(
                self.store,
                self.project_id,
                run_name,
                contracts,
                lambda: None,
                snapshot_root=self._snapshot_root,
            )
            inputs = [
                self._node(node_id, {NodeType.DATASET, NodeType.ARTIFACT, NodeType.SOURCE_SNAPSHOT})
                for node_id in sorted(request.input_ids)
            ]
            states = {node.id: self._run_state(node) for node in [transformation, *inputs]}
        paths = {node.id: node.data.get("path") for node in inputs}
        fingerprints, file_hashes = self._run_observations(inputs, states, paths)
        gaps = ["execution_not_observed", "environment_unknown"]
        if request.code_reference is None:
            gaps.append("code_reference_unknown")
        if any(
            node.type != NodeType.SOURCE_SNAPSHOT
            or file_hashes[node.id] is None
            or paths[node.id]
            != f".lineage/snapshots/{file_hashes[node.id].removeprefix('sha256:')}"
            for node in inputs
        ):
            gaps.append("immutable_input_contents_not_retained")
        gaps.extend(
            f"input_file_unavailable:{node.id}"
            for node in inputs
            if file_hashes[node.id] is None and node.type != NodeType.DATASET
        )
        start = request.model_dump(mode="json", exclude={"action"})
        start["input_ids"] = sorted(request.input_ids)
        data = {
            "status": "started",
            "capture_mode": "reported",
            "reproducible": False,
            "start_request": start,
            "run_name": run_name,
            "transformation_id": transformation.id,
            "transformation_revision": revision(transformation.data),
            "params": request.params,
            "code_reference": request.code_reference,
            "environment": None,
            "policy_hash": policy_hash,
            "validated_contracts": [
                c.model_dump(mode="json") for c in contracts if is_blocked(run_name, set(c.blocks))
            ],
            "input_fingerprints": fingerprints,
            "input_file_hashes": file_hashes,
            "output_fingerprints": {},
            "output_file_hashes": {},
            "capture_gaps": gaps,
        }
        node = Node(
            project_id=self.project_id,
            type=NodeType.RUN,
            natural_key=request.execution_key,
            data=data,
        )

        def commit():
            self._check_run_states(states)
            previous = self.store.get_node(self.project_id, node.id)
            if previous is not None:
                # Finishing does not make a delayed start create another execution.
                keys = (
                    "start_request",
                    "transformation_revision",
                    "policy_hash",
                    "input_fingerprints",
                )
                if previous.data.get("capture_mode") != "reported" or any(
                    previous.data.get(key) != data[key] for key in keys
                ):
                    raise ServiceError(
                        "conflict", "execution key already records a different start"
                    )
                return self._run_result(previous)
            changes = _Changes()
            self._write_node(node, None, changes)
            self._write_edge(
                Edge(
                    project_id=self.project_id,
                    type=EdgeType.GENERATED_BY,
                    src=node.id,
                    dst=transformation.id,
                ),
                None,
                changes,
            )
            for item in inputs:
                self._write_edge(
                    Edge(
                        project_id=self.project_id,
                        type=EdgeType.USED_INPUT,
                        src=node.id,
                        dst=item.id,
                    ),
                    None,
                    changes,
                )
            return self._run_result(self._node(node.id))

        with self.store.transaction():
            return run_guarded(
                self.store,
                self.project_id,
                run_name,
                contracts,
                commit,
                snapshot_root=self._snapshot_root,
            )

    def _finish_run(self, request: op.FinishRunRequest) -> op.RunResult:
        if len({item.node_id for item in request.outputs}) != len(request.outputs):
            raise ServiceError("invalid_request", "duplicate run output IDs")
        with self.store.transaction(write=False):
            run = self._node(request.run_id, {NodeType.RUN})
            if run.data.get("capture_mode") != "reported" or "start_request" not in run.data:
                raise ServiceError("invalid_reference", "run has no reported start receipt")
            if run.data.get("status") == "started":
                self._check_revision(run.data, request.expected_revision, node_id=run.id)
            nodes = [
                self._node(item.node_id, {NodeType.DATASET, NodeType.ARTIFACT, NodeType.INDICATOR})
                for item in sorted(request.outputs, key=lambda item: item.node_id)
            ]
            states = {node.id: self._run_state(node) for node in nodes}
        declared = {item.node_id: item.path for item in request.outputs}
        paths = {
            node.id: declared[node.id] if declared[node.id] is not None else node.data.get("path")
            for node in nodes
        }
        for node in nodes:
            registered = node.data.get("path")
            if (
                registered is not None
                and declared[node.id] is not None
                and registered != declared[node.id]
            ):
                raise ServiceError(
                    "invalid_reference",
                    "output path disagrees with its registered path",
                    node_id=node.id,
                )
        fingerprints, file_hashes = self._run_observations(
            nodes, states, paths, allow_missing=request.status != "succeeded"
        )
        report = {
            "status": request.status,
            "failure_detail": request.failure_detail,
            "outputs": [{"node_id": node.id, "path": paths[node.id]} for node in nodes],
            "output_fingerprints": fingerprints,
            "output_file_hashes": file_hashes,
        }
        with self.store.transaction():
            current = self._node(run.id, {NodeType.RUN})
            self._check_run_states(states)
            if current.data.get("status") != "started":
                if current.data.get("finish_report") != report:
                    raise ServiceError("conflict", "terminal run reports are immutable")
                return self._run_result(current)
            self._check_revision(current.data, request.expected_revision, node_id=run.id)
            gaps = current.data["capture_gaps"] + [
                f"output_file_unavailable:{node.id}"
                for node in nodes
                if file_hashes[node.id] is None
                and (paths[node.id] is not None or node.type == NodeType.ARTIFACT)
            ]
            updated = current.model_copy(
                update={
                    "data": {
                        **current.data,
                        "status": request.status,
                        "failure_detail": request.failure_detail,
                        "finish_report": report,
                        "finished_at": datetime.now(UTC).isoformat(),
                        "output_fingerprints": fingerprints,
                        "output_file_hashes": file_hashes,
                        "capture_gaps": gaps,
                    }
                }
            )
            changes = _Changes()
            self._write_node(updated, request.expected_revision, changes)
            for node in nodes:
                if paths[node.id] is not None and file_hashes[node.id] is None:
                    continue  # A known missing file is a gap, not a produced artifact.
                self._write_edge(
                    Edge(
                        project_id=self.project_id, type=EdgeType.PRODUCED, src=run.id, dst=node.id
                    ),
                    None,
                    changes,
                )
            return self._run_result(self._node(run.id))

    def _content(self, node_id: str) -> op.ContentRef:
        """Return the retained bytes for a snapshot, re-checking the hash on read.

        Capture without read-back is a one-way door: the reference consumer had to
        open `.lineage/snapshots/<hash>` by hand because nothing here would hand
        the bytes back. `verified` is computed from the bytes actually read, never
        inferred from the filename, so a tampered blob comes back inspectable
        (`verified: False`) instead of raising.

        `text` is decoded from the *entire* payload, using the same detection and
        decoding decision `verified_locator`'s anchor check makes on the same
        bytes (`xyle_trace.core.locators.decode_text`) — never a byte-count
        prefix. A truncated preview next to `verified: true` would let a caller
        mistake a partial document for the whole one; a locator could pass
        against bytes this method never showed the caller.
        """
        node = self._node(node_id, types={NodeType.SOURCE_SNAPSHOT})
        data = SnapshotData.model_validate(node.data)
        snapshot_root = self._snapshot_root
        if snapshot_root is None:
            raise ServiceError("invalid_request", "content mode requires a configured project root")
        path = snapshot_root / data.content_hash.removeprefix("sha256:")
        try:
            payload = read_snapshot(
                snapshot_root, data.content_hash, max_bytes=self._capture.limits.max_bytes
            )
        except (OSError, CaptureError) as error:
            raise ServiceError("not_found", "captured bytes unavailable or unsafe") from error
        verified = hash_bytes(payload) == data.content_hash
        text = decode_text(payload)
        return op.ContentRef(
            path=str(path),
            content_hash=data.content_hash,
            size_bytes=len(payload),
            verified=verified,
            text=text,
        )

    def query(self, request: op.QueryRequest | dict) -> op.QueryResult:
        with self._request(_QUERY, request, read_only=True) as parsed:
            node = self._node(parsed.node_id)
            if parsed.mode == "content":
                # Every query response includes the requested node alongside
                # reached nodes (docs/core-contract.md); content mode reaches
                # no other nodes, but the snapshot itself still belongs in
                # `nodes` so callers don't need a second round trip for it.
                return op.QueryResult(
                    mode=parsed.mode,
                    node_id=parsed.node_id,
                    nodes=[self._node_state(node)],
                    content=self._content(parsed.node_id),
                )
            claims = []
            if parsed.mode == "explain":
                graph = traversal.explain(self.store, self.project_id, parsed.node_id)
            else:
                if parsed.mode == "impact":
                    result = impact(self.store, self.project_id, parsed.node_id)
                    ids, claims = result["affected"], result["claims_needing_review"]
                else:
                    walk = traversal.upstream if parsed.mode == "upstream" else traversal.downstream
                    ids = walk(self.store, self.project_id, parsed.node_id, parsed.max_depth)
                graph = traversal.subgraph(self.store, self.project_id, {parsed.node_id, *ids})
            nodes = [self._node_state(Node.model_validate(node)) for node in graph["nodes"]]
            records = []
            if parsed.mode == "explain":
                for state in nodes:
                    if state.node.type == NodeType.DATASET:
                        records.extend(self._record_states(state.node.id))
                        if state.node.natural_key != state.node.id:
                            records.extend(self._record_states(state.node.natural_key))
            return op.QueryResult(
                mode=parsed.mode,
                node_id=parsed.node_id,
                nodes=nodes,
                edges=[
                    op.EdgeState(edge=Edge.model_validate(edge), revision=revision(edge["data"]))
                    for edge in graph["edges"]
                ],
                dependencies=[op.Dependency(**pair) for pair in graph["dependencies"]],
                records=records,
                source_ids=[state.node.id for state in nodes if state.node.type == NodeType.SOURCE],
                claims_needing_review=claims,
            )

    def validate(self, request: op.ValidateRequest | dict) -> op.ValidateResult:
        with self._request(_VALIDATE, request, transaction=False) as parsed:
            contracts, policy_hash = self._policy_snapshot()
            with self.store.transaction(write=False):
                return self._validation(parsed, contracts, policy_hash)

    def _validation(self, request: op.ValidateRequest, contracts: list[Contract], policy_hash: str):
        datasets = {}
        selectors = {contract.dataset for contract in contracts}
        if request.dataset is not None:
            selectors.add(request.dataset)
        for selector in selectors:
            try:
                datasets[selector] = self.store.dataset_node(self.project_id, selector)
            except ValueError:
                datasets[selector] = None  # The core evaluator reports ambiguous selectors.
        if (
            request.dataset is not None
            and datasets[request.dataset] is None
            and not any(contract.dataset == request.dataset for contract in contracts)
        ):
            raise ServiceError("not_found", "dataset selector is not registered or contracted")

        def identity(selector):
            dataset = datasets[selector]
            return dataset.id if dataset else selector

        rows = {}
        evaluations = []
        blocked = set()
        for contract in contracts:
            key = identity(contract.dataset)
            if key not in rows:
                dataset = datasets[contract.dataset]
                states = self._record_states(key)
                if dataset and dataset.natural_key != key:
                    states += self._record_states(dataset.natural_key)
                rows[key] = states
            # Evaluated one contract at a time, with the graph-wide sweeps off
            # (sweeps=False): those run once below, against every contract,
            # rather than once per contract with the other contracts' datasets
            # looking spuriously ungoverned. validate_contracts already stamps
            # each issue with this contract's severity and blocked_patterns.
            issues = validate_contracts(
                self.store,
                self.project_id,
                [contract],
                snapshot_root=self._snapshot_root,
                sweeps=False,
            )
            evaluations.append((contract, key, issues))
            blocked.update(blocked_runs([contract], issues))

        gaps = []
        row_issues = {}
        dataset_issues = set()
        for contract, key, issues in evaluations:
            related = [item for item in contracts if identity(item.dataset) == key]
            allowed = sorted(set.intersection(*(set(item.every_row) for item in related)))
            for issue in issues:
                if issue.kind in {
                    "dataset_not_found",
                    "ambiguous_dataset",
                    "legacy_dataset_reference",
                }:
                    dataset_issues.add(key)
                    state = None
                else:
                    row_issues.setdefault((key, issue.record_key), set()).add(issue.kind)
                    state = next(
                        (row for row in rows[key] if row.record.key == issue.record_key), None
                    )
                if request.dataset is not None and key != identity(request.dataset):
                    continue
                if request.run_name is not None and not is_blocked(
                    request.run_name, set(contract.blocks)
                ):
                    continue
                dataset = datasets[contract.dataset]
                gaps.append(
                    op.Gap(
                        violation=issue,
                        dataset_id=dataset.id if dataset else None,
                        record=state,
                        permitted_statuses=allowed,
                        requirements=related,
                    )
                )

        # Whole-project sweeps — datasets no contract governs, and artifacts
        # nothing produced — are properties of the graph, not of any one
        # contract, so they are computed once against every contract rather
        # than once per contract (which would make each contract's own
        # datasets look ungoverned to every other contract's evaluation).
        sweep_kinds = {"ungoverned_dataset", "orphan_artifact"}
        sweep = validate_contracts(
            self.store, self.project_id, contracts, snapshot_root=self._snapshot_root, sweeps=True
        )
        for violation in sweep:
            if violation.kind not in sweep_kinds:
                continue
            try:
                dataset = self.store.dataset_node(self.project_id, violation.dataset)
            except ValueError:
                dataset = None
            key = dataset.id if dataset else violation.dataset
            if request.dataset is not None and key != identity(request.dataset):
                continue
            if request.run_name is not None:
                continue  # graph-wide warnings have no contract and block no run
            gaps.append(
                op.Gap(
                    violation=violation,
                    dataset_id=dataset.id if dataset else None,
                    record=None,
                    permitted_statuses=[],
                    requirements=[],
                )
            )

        counts = {"total": 0, "assumptions": 0, "unresolved": 0, "invalid": 0, "provisional": 0}
        for key, states in rows.items():
            for state in states:
                counts["total"] += 1
                issues = row_issues.get((key, state.record.key), set())
                if key in dataset_issues:
                    counts["invalid"] += 1
                elif state.resolution is None:
                    counts["unresolved"] += 1
                elif "provisional_backing" in issues:
                    counts["provisional"] += 1
                elif issues:
                    counts["invalid"] += 1
                elif state.resolution.status == "assumption_backed":
                    counts["assumptions"] += 1
        # `valid` reflects every evaluated contract, not just the (possibly
        # dataset/run-scoped) gaps shown above; a warn-severity violation is
        # reported but never flips it. Ungoverned datasets are always warn, so
        # they are correctly excluded here without needing to be considered.
        return op.ValidateResult(
            valid=not any(
                issue.severity == "block" for _, _, issues in evaluations for issue in issues
            ),
            gaps=gaps,
            blocked_patterns=sorted(blocked),
            run_blocked=is_blocked(request.run_name, blocked)
            if request.run_name is not None
            else None,
            policy_hash=policy_hash,
            assumptions=op.AssumptionMetrics(
                **counts, rate=counts["assumptions"] / counts["total"] if counts["total"] else None
            ),
        )

    def _export_destination(self, destination: str) -> Path:
        if self.root is None:
            raise ServiceError("invalid_request", "file export requires a configured root")
        relative = Path(destination)
        if relative.is_absolute():
            raise ServiceError("invalid_request", "export requires a project-relative destination")
        path = (self.root / relative).resolve()
        snapshots = (self.root / ".lineage/snapshots").resolve()
        protected = [self.store.path.resolve()]
        protected += [Path(str(protected[0]) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        if self.contracts_path is not None:
            protected.append(self.contracts_path.resolve())
        if path.is_file() and path.stat().st_nlink > 1 and snapshots.is_dir():
            protected.extend(item for item in snapshots.iterdir() if item.is_file())
        if (
            not path.is_relative_to(self.root)
            or path.is_relative_to(snapshots)
            or path == self.root
            or path.is_dir()
            or any(
                path == item or (path.exists() and item.exists() and path.samefile(item))
                for item in protected
            )
        ):
            raise ServiceError(
                "invalid_request", "export destination is outside the root or protected"
            )
        return path

    def export(self, request: op.ExportRequest | dict) -> op.ExportResult:
        with self._request(_EXPORT, request, transaction=False) as parsed:
            temporary = None
            try:
                path = self._export_destination(parsed.destination) if parsed.destination else None
                with self.store.transaction(write=False):
                    graph = export_graph(self.store, self.project_id)
                    rendered = (
                        render_html(
                            graph,
                            [
                                {"consumer": consumer, "dependency": dependency}
                                for consumer, dependency in _dependencies(
                                    self.store, self.project_id
                                )
                            ],
                        )
                        if parsed.format == "html"
                        else canonical_json(graph)
                    )
                data = rendered.encode("utf-8")
                if path is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(
                        dir=path.parent, prefix=".export-", delete=False
                    ) as output:
                        temporary = Path(output.name)
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, path)
                return op.ExportResult(
                    graph=graph if path is None else None,
                    path=path.relative_to(self.root).as_posix() if path is not None else None,
                    content_hash=hash_bytes(data),
                    node_count=len(graph["nodes"]),
                    edge_count=len(graph["edges"]),
                )
            except (OSError, ValueError) as error:
                if isinstance(error, ServiceError):
                    raise
                raise ServiceError(
                    "invalid_request", "export destination could not be written"
                ) from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def snapshot(self, request: op.SnapshotRequest | dict) -> op.SnapshotResult:
        with self._request(_SNAPSHOT, request, transaction=False) as parsed:
            source = self._node(parsed.source_id, {NodeType.SOURCE})
            if self._capture is None:
                raise ServiceError("capture_failed", "snapshot capture requires a configured root")
            if self.store.in_transaction:
                raise ServiceError(
                    "capture_failed", "capture cannot run inside a write transaction"
                )
            try:
                if parsed.kind == "local":
                    captured = self._capture.local(parsed.path)
                else:
                    uri = source.data.get("uri")
                    if not isinstance(uri, str):
                        raise ServiceError("invalid_reference", "source has no registered URL")
                    captured = self._capture.http(uri)
            except CaptureError as error:
                raise ServiceError("capture_failed", str(error)) from error
            identity = {"source_id": source.id, "content_hash": captured.content_hash}
            node = Node(
                project_id=self.project_id,
                type=NodeType.SOURCE_SNAPSHOT,
                natural_key=canonical_json(identity),
                data={
                    **identity,
                    "size_bytes": captured.size_bytes,
                    "path": captured.path,
                    "retrieval": captured.retrieval,
                },
            )
            with self.store.transaction():
                current_source = self._node(source.id, {NodeType.SOURCE})
                self._check_revision(current_source.data, revision(source.data), node_id=source.id)
                previous = self.store.get_node(self.project_id, node.id)
                if previous is not None:
                    if any(
                        previous.data.get(key) != node.data[key]
                        for key in (*identity, "size_bytes", "path")
                    ):
                        raise ServiceError(
                            "capture_failed", "existing snapshot metadata is inconsistent"
                        )
                    node = previous  # Preserve the first retrieval metadata and timestamp.
                else:
                    node = self.store.upsert_node(node)
                return op.SnapshotResult(
                    snapshot=self._node_state(node),
                    content_hash=captured.content_hash,
                    size_bytes=captured.size_bytes,
                    path=captured.path,
                    already_existed=previous is not None,
                )

    @staticmethod
    def _check_revision(current, expected, *, desired=None, **resource) -> bool:
        """Check strictly, or accept an exact retry when a desired value is supplied."""
        try:
            if desired is not None:
                return check_revision(current, desired, expected)
            require_revision(current, expected)
            return False
        except RevisionConflictError as e:
            raise ServiceError(
                "conflict", str(e), expected=e.expected, actual=e.actual, **resource
            ) from e

    def _node(self, node_id: str, types: set[NodeType] | None = None) -> Node:
        node = self.store.get_node(self.project_id, node_id)
        if node is None:
            raise ServiceError("not_found", f"node {node_id!r} not found in this project")
        if types is not None and node.type not in types:
            raise ServiceError("invalid_reference", f"node {node_id!r} has the wrong type")
        return node

    def _dataset(self, selector: str) -> Node:
        try:
            node = self.store.dataset_node(self.project_id, selector)
        except ValueError as e:
            raise ServiceError("invalid_reference", str(e)) from e
        if node is None:
            raise ServiceError("not_found", f"dataset {selector!r} not found in this project")
        if node.natural_key != node.id and node.natural_key in self.store.dataset_ids(
            self.project_id
        ):
            raise ServiceError("invalid_reference", "legacy dataset records require reconciliation")
        return node

    def _claim_ids(self, node_id: str) -> set[str]:
        ids = set(impact(self.store, self.project_id, node_id)["claims_needing_review"])
        node = self.store.get_node(self.project_id, node_id)
        if node and node.type == NodeType.CLAIM:
            ids.add(node_id)
        return ids

    @staticmethod
    def _node_state(node: Node) -> op.NodeState:
        return op.NodeState(node=node, revision=revision(node.data))

    @staticmethod
    def _resolution_data(resolution: Resolution | None) -> dict | None:
        return (
            {"status": resolution.status, "target_id": resolution.target_id} if resolution else None
        )

    def _write_node(self, node: Node, expected: str | None, changes: _Changes) -> Node:
        current = self.store.get_node(self.project_id, node.id)
        if node.type == NodeType.CLAIM:
            if current:
                for key in ("review_status", "review_decision_id"):
                    if key in current.data:
                        node.data[key] = current.data[key]
                if revision(node.data) != revision(current.data):
                    node.data["review_status"] = "needs_review"
            else:
                node.data["review_status"] = "needs_review"
        if not self._check_revision(
            current.data if current else None, expected, desired=node.data, node_id=node.id
        ):
            return current
        if current:
            changes.claims.update(self._claim_ids(node.id))
            for dataset_id in self.store.dataset_ids(self.project_id):
                for key, backing in self.store.resolutions(
                    self.project_id, dataset_id, resolve=False
                ).items():
                    if backing.target_id == node.id:
                        changes.records.add((dataset_id, key))
        return self.store.upsert_node(node)

    def _write_records(self, dataset_id: str, writes: list[op.RecordWrite], changes: _Changes):
        current = {r.key: r for r in self.store.records(self.project_id, dataset_id, resolve=False)}
        seen = set()
        pending = []
        for write in writes:
            if write.key in seen:
                raise ServiceError("invalid_request", f"duplicate record key {write.key!r}")
            seen.add(write.key)
            record = current.get(write.key)
            if self._check_revision(
                record.data if record else None,
                write.expected_revision,
                desired=write.data,
                dataset_id=dataset_id,
                record_key=write.key,
            ):
                pending.append(
                    Record(
                        project_id=self.project_id,
                        dataset_id=dataset_id,
                        key=write.key,
                        data=write.data,
                    )
                )
            changes.records.add((dataset_id, write.key))
        if pending:
            changes.claims.update(self._claim_ids(dataset_id))
            self.store.put_records(pending)

    def _bindings(self, bindings: list[op.RecordBinding], target: Node) -> list[Resolution]:
        if bindings and target.data["provisional"]:
            raise ServiceError("invalid_reference", "provisional backing cannot resolve records")
        seen = set()
        result = []
        for binding in bindings:
            dataset = self._dataset(binding.dataset_id)
            pair = (dataset.id, binding.record_key)
            if pair in seen:
                raise ServiceError("invalid_request", "duplicate record binding")
            seen.add(pair)
            record = next(
                (
                    r
                    for r in self.store.records(self.project_id, dataset.id, resolve=False)
                    if r.key == binding.record_key
                ),
                None,
            )
            if record is None:
                raise ServiceError("not_found", f"record {binding.record_key!r} not found")
            self._check_revision(
                record.data,
                binding.expected_record_revision,
                dataset_id=dataset.id,
                record_key=record.key,
            )
            desired = Resolution(
                project_id=self.project_id,
                dataset_id=dataset.id,
                record_key=record.key,
                target_id=target.id,
                status="evidence_backed"
                if target.type == NodeType.EVIDENCE_LINK
                else "assumption_backed",
            )
            current = self.store.resolutions(self.project_id, dataset.id, resolve=False).get(
                record.key
            )
            self._check_revision(
                self._resolution_data(current),
                binding.expected_resolution_revision,
                desired=self._resolution_data(desired),
                dataset_id=dataset.id,
                record_key=record.key,
            )
            result.append(desired)
        return result

    def _apply_bindings(self, bindings: list[Resolution], changes: _Changes):
        for binding in bindings:
            changes.records.add((binding.dataset_id, binding.record_key))
            current = self.store.resolutions(
                self.project_id, binding.dataset_id, resolve=False
            ).get(binding.record_key)
            if self._resolution_data(current) != self._resolution_data(binding):
                changes.claims.update(self._claim_ids(binding.dataset_id))
            self.store.put_resolution(binding)

    def _result(self, node_id: str, changes: _Changes) -> op.MutationResult:
        records = []
        for dataset_id, key in sorted(changes.records):
            record = next(
                (
                    r
                    for r in self.store.records(self.project_id, dataset_id, resolve=False)
                    if r.key == key
                ),
                None,
            )
            if record is None:
                continue
            backing = self.store.resolutions(self.project_id, dataset_id, resolve=False).get(key)
            records.append(
                op.RecordState(
                    record=record,
                    revision=revision(record.data),
                    resolution=backing,
                    resolution_revision=revision(self._resolution_data(backing))
                    if backing
                    else None,
                )
            )
        violations = []
        touched_datasets = {dataset for dataset, _ in changes.records}
        for violation in validate_contracts(
            self.store, self.project_id, self.contracts, snapshot_root=self._snapshot_root
        ):
            try:
                dataset = self.store.dataset_node(self.project_id, violation.dataset)
            except ValueError:
                # The evaluator has already reported this selector as ambiguous.
                dataset = None
            dataset_id = dataset.id if dataset else violation.dataset
            if (dataset_id, violation.record_key) in changes.records or (
                not violation.record_key and dataset_id in touched_datasets
            ):
                violations.append(violation)
        return op.MutationResult(
            node=self._node_state(self._node(node_id)),
            records=records,
            claims=[self._node_state(self._node(key)) for key in sorted(changes.claims)],
            violations=violations,
        )

    def register(self, request: op.RegisterRequest | dict) -> op.MutationResult:
        with self._request(_REGISTER, request) as parsed:
            changes = _Changes()
            node = Node(
                project_id=self.project_id,
                type=NodeType(parsed.type),
                natural_key=parsed.natural_key,
                data=parsed.data.model_dump(mode="json"),
            )
            node = self._write_node(node, parsed.expected_revision, changes)
            if isinstance(parsed, op.DatasetRegister):
                self._dataset(node.id)
                self._write_records(node.id, parsed.records, changes)
            return self._result(node.id, changes)

    def _write_edge(
        self, edge: Edge, expected: str | None, changes: _Changes, *, review=False
    ) -> Edge:
        src, dst = self._node(edge.src), self._node(edge.dst)
        rules = {
            EdgeType.SNAPSHOT_OF: ({NodeType.SOURCE_SNAPSHOT}, {NodeType.SOURCE}),
            EdgeType.USED_INPUT: (
                {NodeType.RUN},
                {NodeType.DATASET, NodeType.SOURCE_SNAPSHOT, NodeType.ARTIFACT},
            ),
            EdgeType.PRODUCED: (
                {NodeType.RUN},
                {NodeType.DATASET, NodeType.ARTIFACT, NodeType.INDICATOR},
            ),
            EdgeType.SUPPORTS: (
                {
                    NodeType.DATASET,
                    NodeType.ARTIFACT,
                    NodeType.INDICATOR,
                    NodeType.EVIDENCE_LINK,
                    NodeType.SOURCE_SNAPSHOT,
                },
                {NodeType.CLAIM},
            ),
            EdgeType.CONTRADICTS: (
                {
                    NodeType.DATASET,
                    NodeType.ARTIFACT,
                    NodeType.INDICATOR,
                    NodeType.EVIDENCE_LINK,
                    NodeType.SOURCE_SNAPSHOT,
                },
                {NodeType.CLAIM},
            ),
            EdgeType.APPEARS_IN: (
                {NodeType.CLAIM, NodeType.INDICATOR, NodeType.ARTIFACT},
                {NodeType.ARTIFACT},
            ),
        }
        if edge.type in rules and (
            src.type not in rules[edge.type][0] or dst.type not in rules[edge.type][1]
        ):
            raise ServiceError(
                "invalid_reference", "edge endpoint types do not match the relationship"
            )
        if edge.type == EdgeType.REVIEWED_BY and dst.type != NodeType.DECISION:
            raise ServiceError("invalid_reference", "reviewed_by must reference a decision")
        if edge.type == EdgeType.SNAPSHOT_OF and src.data.get("source_id") != dst.id:
            raise ServiceError(
                "invalid_reference", "snapshot_of disagrees with the snapshot source"
            )
        current = self.store.get_edge(self.project_id, edge.id)
        if self._check_revision(
            current.data if current else None, expected, desired=edge.data, edge_id=edge.id
        ):
            if not review:
                affected = self._claim_ids(src.id) | self._claim_ids(dst.id)
                changes.claims.update(affected)
                self.store.mark_claims_for_review(self.project_id, sorted(affected))
            self.store.upsert_edge(edge)
        return self.store.get_edge(self.project_id, edge.id)

    def link(self, request: op.LinkRequest | dict) -> op.MutationResult | op.EdgeResult:
        with self._request(_LINK, request) as parsed:
            changes = _Changes()
            if isinstance(parsed, op.EdgeLinkRequest):
                edge = Edge(
                    project_id=self.project_id,
                    type=parsed.type,
                    src=parsed.src,
                    dst=parsed.dst,
                    # exclude_none: EdgeQualifiers.locator is the same optional Locator
                    # as EvidenceInput's; an absent anchor (or locator, or rationale)
                    # should not appear in stored data any more than it does there.
                    data=parsed.data.model_dump(mode="json", exclude_none=True),
                )
                edge = self._write_edge(edge, parsed.expected_revision, changes)
                return op.EdgeResult(
                    edge=op.EdgeState(edge=edge, revision=revision(edge.data)),
                    claims=[self._node_state(self._node(key)) for key in sorted(changes.claims)],
                )
            node = Node(
                project_id=self.project_id,
                type=NodeType.EVIDENCE_LINK,
                natural_key=parsed.natural_key,
                # exclude_none: an absent locator anchor should not appear in stored
                # data any more than an absent locator itself does; both are the
                # optional-field default rather than an observed value.
                data=parsed.data.model_dump(mode="json", exclude_none=True),
            )
            # Provisional evidence is still required to reference real source material.
            observed = node.model_copy(update={"data": {**node.data, "provisional": False}})
            issue = backing_issue(self.store, self.project_id, observed, False)
            if issue:
                raise ServiceError("invalid_reference", issue[1])
            bindings = self._bindings(parsed.records, node)
            self._write_node(node, parsed.expected_revision, changes)
            self._apply_bindings(bindings, changes)
            return self._result(node.id, changes)

    def decide(self, request: op.DecideRequest | dict) -> op.MutationResult:
        with self._request(_DECIDE, request) as parsed:
            changes = _Changes()
            data = parsed.model_dump(
                mode="json", exclude={"natural_key", "expected_revision", "records"}
            )
            node = Node(
                project_id=self.project_id,
                type=NodeType.DECISION,
                natural_key=parsed.natural_key,
                data=data,
            )
            current = self.store.get_node(self.project_id, node.id)
            if current and current.data.get("kind", "assumption") != parsed.kind:
                raise ServiceError(
                    "invalid_reference", "a decision cannot change kind; use a new natural key"
                )
            if isinstance(parsed, op.ReviewRequest):
                return self._review(parsed, node, current, changes)
            bindings = (
                self._bindings(parsed.records, node)
                if isinstance(parsed, op.AssumptionRequest)
                else []
            )
            if isinstance(parsed, op.SubjectDecisionRequest):
                if len(set(parsed.subject_ids)) != len(parsed.subject_ids):
                    raise ServiceError("invalid_request", "duplicate decision subjects")
                for subject_id in parsed.subject_ids:
                    self._node(subject_id)
            self._write_node(node, parsed.expected_revision, changes)
            if isinstance(parsed, op.CorrectionRequest):
                grouped = {}
                for change in parsed.changes:
                    dataset = self._dataset(change.dataset_id)
                    grouped.setdefault(dataset.id, []).append(change)
                for dataset_id, writes in grouped.items():
                    self._write_records(dataset_id, writes, changes)
            self._apply_bindings(bindings, changes)
            return self._result(node.id, changes)

    def _review(
        self, request: op.ReviewRequest, node: Node, current: Node | None, changes: _Changes
    ) -> op.MutationResult:
        if len({subject.claim_id for subject in request.claims}) != len(request.claims):
            raise ServiceError("invalid_request", "duplicate review subjects")
        subjects = {
            subject.claim_id: self._node(subject.claim_id, {NodeType.CLAIM})
            for subject in request.claims
        }
        if current:
            prior_data = {
                key: value
                for key, value in current.data.items()
                if key != "reviewed_claim_revisions"
            }
            if revision(prior_data) != revision(node.data):
                raise ServiceError(
                    "conflict", "review decisions are immutable; record a new review"
                )
            receipts = current.data.get("reviewed_claim_revisions", {})
            for claim_id, claim in subjects.items():
                self._check_revision(claim.data, receipts.get(claim_id), node_id=claim_id)
            changes.claims.update(subjects)
            return self._result(node.id, changes)
        self._check_revision(None, request.expected_revision, node_id=node.id)
        receipts = {}
        for subject in request.claims:
            claim = subjects[subject.claim_id]
            self._check_revision(claim.data, subject.expected_revision, node_id=claim.id)
            receipts[claim.id] = revision(
                {
                    **claim.data,
                    "review_status": subject.review_status,
                    "review_decision_id": node.id,
                }
            )
        node.data["reviewed_claim_revisions"] = receipts
        self._write_node(node, None, changes)
        for subject in request.claims:
            self.store.review_claim(
                self.project_id, subject.claim_id, node.id, subject.review_status
            )
            edge = Edge(
                project_id=self.project_id,
                type=EdgeType.REVIEWED_BY,
                src=subject.claim_id,
                dst=node.id,
                data={"provisional": False},
            )
            self._write_edge(edge, None, changes, review=True)
            changes.claims.add(subject.claim_id)
        return self._result(node.id, changes)
