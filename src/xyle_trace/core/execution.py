"""Local Python/JSON execution and verified replay of retained inputs.

Programs are trusted project code, not sandboxed code. They receive input.json and
output.json paths as argv[1:3], run with isolated Python flags and no site packages,
and must write a finite JSON value. No shell command interpolation is used.
"""

import base64
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from xyle_trace.contracts.validator import run_guarded
from xyle_trace.core.handoff import read_project_file
from xyle_trace.core.hashing import hash_bytes, hash_file
from xyle_trace.core.ids import make_id
from xyle_trace.core.revisions import canonical_json, revision
from xyle_trace.core.snapshots import SnapshotCapture, read_snapshot
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType
from xyle_trace.service import Service, ServiceError


def environment():
    return {
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable_hash": hash_file(Path(sys.executable).resolve()),
        "flags": ["-I", "-S"],
    }


def _read_blob(root, reference):
    digest = reference["content_hash"]
    expected = f".lineage/snapshots/{digest.removeprefix('sha256:')}"
    path = root / expected
    if reference["path"] != expected or not path.resolve().is_relative_to(root):
        raise ValueError("retained content path is invalid")
    data = read_snapshot(root / ".lineage" / "snapshots", digest)
    if hash_bytes(data) != digest:
        raise ValueError("retained content hash does not match")
    return data


def _retain(capture, value):
    blob = capture.bytes(canonical_json(value).encode("utf-8"))
    return {"path": blob.path, "content_hash": blob.content_hash}


def _working_path(service, out, *, script=None, input_ids=()):
    """Validate a portable, project-relative working path without creating it."""
    if (
        not isinstance(out, str)
        or not out
        or "\\" in out
        or Path(out).is_absolute()
        or any(part in {"", ".", ".."} for part in out.split("/"))
        or (len(out) >= 2 and out[0].isalpha() and out[1] == ":")
    ):
        raise ValueError("working output requires a project-relative path without traversal")
    protected_names = {
        ".lineage",
        ".git",
        ".codex",
        ".agents",
        ".claude",
        ".aws",
        ".ssh",
        ".config",
        ".kube",
        ".gnupg",
        ".venv",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".mcp.json",
        "agents.md",
        "claude.md",
    }
    if any(
        part.casefold() in protected_names
        or part.casefold() == ".env"
        or part.casefold().startswith(".env.")
        for part in Path(out).parts
    ):
        raise ValueError("working output path is protected")
    current = service.root
    for part in Path(out).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("working output paths must not be symlinks")
    protected = [service.store.path, service.contracts_path]
    protected += [Path(str(service.store.path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    references = [script]
    for node in service.store.all_nodes(service.project_id):
        if node.id in input_ids:
            references.append(node.data.get("path"))
        if node.type == NodeType.SOURCE:
            uri = node.data.get("uri")
            if isinstance(uri, str) and "://" not in uri:
                references.append(uri)
        if node.type == NodeType.SOURCE_SNAPSHOT:
            retrieval = node.data.get("retrieval", {})
            if isinstance(retrieval, dict) and retrieval.get("kind") == "local":
                references.append(retrieval.get("path"))
        if node.type == NodeType.TRANSFORMATION:
            references.append(node.data.get("code_reference"))
        if node.type == NodeType.RUN:
            recorded = node.data.get("execution_request")
            if isinstance(recorded, dict):
                references.append(recorded.get("script"))
    protected += [service.root / value for value in references if isinstance(value, str) and value]
    if any(
        path is not None
        and (
            current.resolve() == path.resolve()
            or (current.exists() and path.exists() and current.samefile(path))
        )
        for path in protected
    ):
        raise ValueError("working output overlaps an input, source, script, policy or database")
    if current.exists() and (not current.is_file() or current.stat().st_nlink != 1):
        raise ValueError("working output must be a regular file without hard links")
    return current


def _working_state(service, out, max_bytes, **paths):
    path = _working_path(service, out, **paths)
    alias_id = make_id(service.project_id, "artifact", f"working-output:{out}")
    alias = service.store.get_node(service.project_id, alias_id)
    try:
        observed = hash_bytes(read_project_file(service.root, out, max_bytes=max_bytes))
    except FileNotFoundError:
        observed = None
    if alias is None and observed is not None:
        raise ServiceError("conflict", "working output already exists without a runner receipt")
    if alias is not None and (
        alias.data.get("path") != out
        or observed is None
        or alias.data.get("content_hash") != observed
    ):
        raise ServiceError("conflict", "working output is missing or differs from its receipt")
    return path, {"revision": revision(alias.data) if alias else None, "content_hash": observed}


def _check_working(service, out, expected, max_bytes, **paths):
    path, current = _working_state(service, out, max_bytes, **paths)
    if current != expected:
        raise ServiceError("conflict", "working output changed during execution")
    return path


def _publish_working(service, run, out, expected, payload, max_bytes, **paths):
    path = _check_working(service, out, expected, max_bytes, **paths)
    temporary = None
    created = []
    parent = path.parent
    while not parent.exists():
        created.append(parent)
        parent = parent.parent
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".xyle-output-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # The caller holds a write transaction: recheck the alias revision and
        # previous bytes immediately before replacing the working directory entry.
        _check_working(service, out, expected, max_bytes, **paths)
        alias = service.store.upsert_node(
            Node(
                project_id=service.project_id,
                type=NodeType.ARTIFACT,
                natural_key=f"working-output:{out}",
                data={
                    "path": out,
                    "content_hash": hash_bytes(payload),
                    "description": "Working JSON output",
                },
            )
        )
        service.store.upsert_edge(
            Edge(
                project_id=service.project_id,
                type=EdgeType.PRODUCED,
                src=run.id,
                dst=alias.id,
                data={"provisional": False},
            )
        )
        os.replace(temporary, path)
        temporary = None
        return {"node_id": alias.id, "path": out, "content_hash": hash_bytes(payload)}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if temporary is not None or not path.exists():
            for directory in created:
                try:
                    directory.rmdir()
                except OSError:
                    pass


def execute(
    service: Service,
    *,
    key: str,
    transformation_id: str | None = None,
    input_ids: list[str] | None = None,
    script: str | None = None,
    params: dict | None = None,
    replay_of: str | None = None,
    timeout: float = 60,
    out: str | None = None,
) -> dict:
    """Execute once per key; replay uses retained code/data, never current datasets.

    A new execution checks current policy twice, including at reservation. A replay
    uses the original policy receipt, checks environment identity, and compares
    canonical output hashes. It does not authorize consumption of current inputs.
    """
    if not key or key != key.strip() or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("a nonempty key and positive finite timeout are required")
    if params is not None and not isinstance(params, dict):
        raise ValueError("parameters must be a JSON object")
    params = json.loads(canonical_json(params)) if params is not None else None
    input_ids = list(input_ids) if input_ids is not None else None
    if service.root is None or service.store.in_transaction:
        raise ValueError("execution needs a project root and no enclosing transaction")
    root = service.root
    capture = SnapshotCapture(root)
    run_id = make_id(service.project_id, "run", key)
    request = {
        "transformation_id": transformation_id,
        "input_ids": input_ids,
        "script": script,
        "params": params,
        "replay_of": replay_of,
        "timeout": timeout,
    }
    if out is not None:
        request["out"] = out
    canonical_json(request)  # Reject nonfinite or non-JSON parameters before any mutation.
    existing = service.store.get_node(service.project_id, run_id)
    if existing:
        if existing.data.get("execution_request") != request:
            raise ServiceError("conflict", "execution key already records a different request")
        if existing.data["status"] == "started":
            raise ServiceError(
                "conflict", "execution is in progress or interrupted; inspect before retry"
            )
        _read_blob(root, existing.data["bundle"])
        if existing.data.get("output"):
            _read_blob(root, existing.data["output"])
        if out is not None and existing.data.get("status") == "succeeded":
            _, state = _working_state(service, out, capture.limits.max_bytes)
            receipt = existing.data.get("working_output", {})
            if receipt.get("content_hash") != state["content_hash"] or receipt.get("path") != out:
                raise ServiceError("conflict", "working output differs from the selected run")
        return existing.model_dump(mode="json")

    working_state = None
    if out is not None:
        _, working_state = _working_state(
            service,
            out,
            capture.limits.max_bytes,
            script=script,
            input_ids=input_ids or (),
        )
    states = {}
    expected_output = None
    if replay_of is not None:
        if any(value is not None for value in (transformation_id, input_ids, script, params)):
            raise ValueError("replay accepts only the original run ID, key, and timeout")
        original = service.store.get_node(service.project_id, replay_of)
        if (
            original is None
            or original.type != NodeType.RUN
            or original.data.get("capture_mode") != "managed"
            or original.data.get("status") != "succeeded"
        ):
            raise ValueError("replay requires a successful managed run")
        bundle_ref = original.data["bundle"]
        bundle = json.loads(_read_blob(root, bundle_ref))
        _read_blob(root, original.data["output"])
        expected_output = original.data["output"]["content_hash"]
        if bundle["environment"] != environment():
            raise ValueError("replay environment differs from the recorded interpreter/platform")
        transformation_id = bundle["transformation_id"]
        input_ids = list(bundle["input"]["inputs"])
    else:
        if (
            not transformation_id
            or not input_ids
            or not script
            or len(set(input_ids)) != len(input_ids)
        ):
            raise ValueError("execute requires a transformation, unique inputs, and a script")
        code_path = (root / script).resolve(strict=True)
        if Path(script).is_absolute() or not code_path.is_relative_to(root):
            raise ValueError("script must be a project-relative file")
        code = code_path.read_text(encoding="utf-8")
        with service.store.transaction(write=False):
            contracts, policy_hash = service._policy_snapshot()
            transformation = service._node(transformation_id, {NodeType.TRANSFORMATION})
            run_guarded(
                service.store,
                service.project_id,
                transformation.natural_key,
                contracts,
                lambda: None,
                snapshot_root=service._snapshot_root,
            )
            frozen = {}
            states[transformation_id] = service._run_state(transformation)
            for node_id in input_ids:
                node = service._node(
                    node_id, {NodeType.DATASET, NodeType.ARTIFACT, NodeType.SOURCE_SNAPSHOT}
                )
                states[node_id] = service._run_state(node)
                # Execution captures its direct current input. Presentation-level
                # explanations may inspect an ancestor's historical execution.
                details = service.query({"mode": "upstream", "node_id": node_id})
                frozen[node_id] = {
                    "node": node.model_dump(mode="json"),
                    "records": [
                        row.model_dump(mode="json")
                        for row in (
                            service._record_states(node_id) if node.type == NodeType.DATASET else []
                        )
                    ],
                    "lineage": details.model_dump(mode="json"),
                }
        for node_id, item in frozen.items():
            if item["node"]["type"] == "dataset":
                continue
            path = item["node"]["data"].get("path")
            if not path:
                raise ValueError("file inputs must have a readable project-relative path")
            copied = capture.local(path)
            if item["node"]["type"] == "source_snapshot" and copied.content_hash != item["node"][
                "data"
            ].get("content_hash"):
                raise ValueError("source snapshot content is corrupt")
            item["file_base64"] = base64.b64encode((root / copied.path).read_bytes()).decode(
                "ascii"
            )
        bundle = {
            "version": 1,
            "transformation_id": transformation_id,
            "code": code,
            "input": {"inputs": frozen, "params": params or {}},
            "environment": environment(),
            "policy_hash": policy_hash,
            "contracts": [contract.model_dump(mode="json") for contract in contracts],
        }
        bundle_ref = _retain(capture, bundle)

    output_key = f"execution:{run_id}:output"
    output_id = make_id(service.project_id, "artifact", output_key)
    run = Node(
        project_id=service.project_id,
        type=NodeType.RUN,
        natural_key=key,
        data={
            "capture_mode": "managed",
            "status": "started",
            "reproducible": False,
            "replay_verified": False,
            "replay_of": replay_of,
            "bundle": bundle_ref,
            "execution_request": request,
            "policy_hash": bundle["policy_hash"],
            "environment": bundle["environment"],
            "output": None,
        },
    )
    with service.store.transaction():
        if service.store.get_node(service.project_id, run_id):
            raise ServiceError("conflict", "execution key was reserved by another worker")
        if service.store.get_node(service.project_id, output_id):
            raise ServiceError("conflict", "execution output identity already exists")
        if out is not None:
            _check_working(
                service,
                out,
                working_state,
                capture.limits.max_bytes,
                script=script,
                input_ids=input_ids or (),
            )
        if replay_of is None:
            contracts, current_hash = service._policy_snapshot()
            if current_hash != bundle["policy_hash"]:
                raise ServiceError("conflict", "policy changed during input capture")
            service._check_run_states(states)
            run_guarded(
                service.store,
                service.project_id,
                transformation.natural_key,
                contracts,
                lambda: None,
                snapshot_root=service._snapshot_root,
            )
        service.store.upsert_node(run)
        edges = [(EdgeType.GENERATED_BY, transformation_id)] + [
            (EdgeType.USED_INPUT, value) for value in input_ids
        ]
        if replay_of:
            edges.append((EdgeType.DEPENDS_ON, replay_of))
        for kind, target in edges:
            service.store.upsert_edge(
                Edge(
                    project_id=service.project_id,
                    type=kind,
                    src=run.id,
                    dst=target,
                    data={"provisional": False},
                )
            )

    try:
        with tempfile.TemporaryDirectory(prefix="xyle-execute-") as working:
            work = Path(working)
            (work / "code.py").write_text(bundle["code"], encoding="utf-8")
            (work / "input.json").write_text(canonical_json(bundle["input"]), encoding="utf-8")
            # Log files avoid unbounded subprocess output in memory. They are not
            # copied to the graph and may contain application-specific secrets.
            with (work / "stdout").open("wb") as stdout, (work / "stderr").open("wb") as stderr:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-S",
                        str(work / "code.py"),
                        "input.json",
                        "output.json",
                    ],
                    cwd=work,
                    env={"PATH": os.defpath, "LANG": "C", "TZ": "UTC"},
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    check=False,
                )
            if process.returncode:
                raise ValueError(f"program exited with code {process.returncode}")
            output_path = work / "output.json"
            if output_path.is_symlink() or not output_path.is_file():
                raise ValueError("program did not produce a regular output.json")
            if output_path.stat().st_size > capture.limits.max_bytes:
                raise ValueError("output exceeds the capture byte limit")
            value = json.loads(
                read_project_file(work, "output.json", max_bytes=capture.limits.max_bytes)
            )
            output = _retain(capture, value)
            run.data["output"] = output
            matches = expected_output is None or output["content_hash"] == expected_output
            run.data.update(
                status="succeeded" if matches else "failed",
                replay_verified=expected_output is not None and matches,
                reproducible=expected_output is not None and matches,
            )
            if not matches:
                run.data["failure_detail"] = "replay output differs from the original output"
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired) as error:
        run.data.update(status="failed", failure_detail=str(error))

    with service.store.transaction():
        collision = service.store.get_node(service.project_id, output_id) is not None
        if collision:
            run.data.update(
                status="failed",
                reproducible=False,
                replay_verified=False,
                failure_detail="output identity changed during execution",
            )
        if run.data["output"] is not None and not collision:
            output_node = service.store.upsert_node(
                Node(
                    project_id=service.project_id,
                    type=NodeType.ARTIFACT,
                    natural_key=output_key,
                    data={**run.data["output"], "description": "Retained execution output"},
                )
            )
            service.store.upsert_edge(
                Edge(
                    project_id=service.project_id,
                    type=EdgeType.PRODUCED,
                    src=run.id,
                    dst=output_node.id,
                    data={"provisional": False},
                )
            )
        if out is not None:
            if run.data["status"] == "succeeded":
                try:
                    with service.store.transaction():
                        working = _publish_working(
                            service,
                            run,
                            out,
                            working_state,
                            _read_blob(root, run.data["output"]),
                            capture.limits.max_bytes,
                            script=script,
                            input_ids=input_ids or (),
                        )
                    run.data["working_output"] = working
                    run.data["output_file_hashes"] = {working["node_id"]: working["content_hash"]}
                except (OSError, ValueError) as error:
                    run.data.update(
                        status="failed",
                        reproducible=False,
                        replay_verified=False,
                        failure_detail=f"working output publication failed: {error}",
                    )
            run.data["finished_at"] = datetime.now(UTC).isoformat()
        service.store.upsert_node(run)
    return run.model_dump(mode="json")
