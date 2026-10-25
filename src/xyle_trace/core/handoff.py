"""Verified local project archives; imported bytes remain opaque retained blobs.

A manifest detects corruption, not publisher identity. The caller trusts the local
root and destination parent against concurrent hostile directory replacement.
No imported code, hooks, environment, or arbitrary filesystem paths are restored.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from xyle_trace.contracts.loader import parse_contracts
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.revisions import canonical_json
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Edge, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store

MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_BLOB_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBERS = 10000
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROTECTED = {".git", ".aws", ".ssh", ".codex", ".agents", ".claude"}


def read_project_file(root: Path, relative: str, *, max_bytes=MAX_MEMBER_BYTES) -> bytes:
    """Read one explicit regular file under root, refusing symlinks and traversal."""
    return _read_regular(_project_path(root, relative), max_bytes)


def hash_project_file(root: Path, relative: str, *, max_bytes=MAX_BLOB_BYTES) -> str:
    """Hash one explicitly referenced file without retaining all its bytes in memory."""
    digest = hashlib.sha256()
    size = 0
    with _regular_stream(_project_path(root, relative), max_bytes) as source:
        while chunk := source.read(min(1024 * 1024, max_bytes - size + 1)):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("file exceeds byte limit")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _project_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        raise ValueError("unsafe project path")
    if any(p in _PROTECTED or p == ".env" or p.startswith(".env.") for p in path.parts):
        raise ValueError("protected project path")
    root = Path(root).resolve(strict=True)
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("unsafe symlink in project path")
    return current


def _read_regular(path: Path, limit: int) -> bytes:
    with _regular_stream(path, limit) as stream:
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("file exceeds byte limit")
        return data


@contextmanager
def _regular_stream(path: Path, limit: int):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("file must be regular")
        if info.st_size > limit:
            raise ValueError("file exceeds byte limit")
        yield stream


def _copy_verified(source, output, limit, *, expected=None):
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(min(1024 * 1024, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise ValueError("content exceeds byte limit")
        digest.update(chunk)
        output.write(chunk)
    actual = "sha256:" + digest.hexdigest()
    if expected is not None and actual != expected:
        raise ValueError("content hash mismatch")
    return {"content_hash": actual, "size_bytes": size}


def _limits(max_blob_bytes, max_total_bytes):
    if any(type(n) is not int or n < 1 for n in (max_blob_bytes, max_total_bytes)):
        raise ValueError("archive limits must be positive integers")


def _check_directory(stream):
    """Bound ZIP central-directory allocation before zipfile parses its entries."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise ValueError("invalid ZIP end record")
    end = struct.unpack("<4s4H2LH", tail[offset : offset + 22])
    if end[1] or end[2] or end[3] != end[4] or offset + 22 + end[7] != len(tail):
        raise ValueError("unsupported ZIP end record")
    count, directory_size = end[4], end[5]
    if count == 65535 or directory_size == 0xFFFFFFFF or end[6] == 0xFFFFFFFF:
        end_position = size - len(tail) + offset
        if end_position < 20:
            raise ValueError("invalid ZIP64 locator")
        stream.seek(end_position - 20)
        locator = struct.unpack("<4sLQL", stream.read(20))
        if locator[0] != b"PK\x06\x07" or locator[1] or locator[3] != 1:
            raise ValueError("unsupported ZIP64 locator")
        stream.seek(locator[2])
        record = stream.read(56)
        if len(record) != 56:
            raise ValueError("invalid ZIP64 end record")
        large = struct.unpack("<4sQ2H2L4Q", record)
        if large[0] != b"PK\x06\x06" or large[1] != 44 or large[4] or large[5]:
            raise ValueError("unsupported ZIP64 end record")
        count, directory_size = large[7], large[8]
    if count > MAX_MEMBERS or directory_size > MAX_MEMBERS * 1024:
        raise ValueError("archive central directory exceeds limit")
    stream.seek(0)


def retained_references(nodes: list[Node]) -> list[dict]:
    """Enumerate explicit retention references, never scan the filesystem."""
    refs = []
    for node in nodes:
        if (
            node.type == NodeType.ARTIFACT
            and isinstance(node.data.get("path"), str)
            and node.data["path"].startswith(".lineage/snapshots/")
        ):
            refs.append(
                {
                    "node_id": node.id,
                    "kind": "artifact",
                    "content_hash": node.data.get("content_hash"),
                    "path": node.data["path"],
                }
            )
        if node.type == NodeType.SOURCE_SNAPSHOT:
            digest = node.data.get("content_hash")
            refs.append(
                {
                    "node_id": node.id,
                    "kind": "snapshot",
                    "content_hash": digest,
                    "path": f".lineage/snapshots/{str(digest).removeprefix('sha256:')}",
                }
            )
        if node.type == NodeType.RUN:
            for key in ("bundle", "output"):
                value = node.data.get(key)
                if value is not None:
                    refs.append(
                        {
                            "node_id": node.id,
                            "kind": key,
                            "content_hash": value.get("content_hash")
                            if isinstance(value, dict)
                            else None,
                            "path": value.get("path") if isinstance(value, dict) else None,
                        }
                    )
    return refs


def _check_reference(ref: dict) -> str:
    digest = ref["content_hash"]
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError("invalid retained content hash")
    if ref["path"] != ".lineage/snapshots/" + digest.removeprefix("sha256:"):
        raise ValueError("invalid retained content path")
    return digest


def _json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("non-finite JSON number")

    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite)
        canonical_json(value)  # Also rejects finite-looking numbers overflowing to infinity.
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid project JSON") from error


def _validate_graph(graph):
    if not isinstance(graph, dict) or not isinstance(graph.get("project_id"), str):
        raise TypeError("invalid project graph")
    project_id = graph["project_id"]
    if not project_id:
        raise ValueError("empty project identity")
    if (
        not isinstance(graph.get("nodes"), list)
        or not isinstance(graph.get("edges"), list)
        or not isinstance(graph.get("datasets"), dict)
    ):
        raise TypeError("invalid graph collections")
    nodes, edges, records, resolutions = [], [], [], []
    by_id = {}
    for raw in graph["nodes"]:
        node = Node.model_validate(raw)
        if node.project_id != project_id or raw.get("id") != node.id or node.id in by_id:
            raise ValueError("invalid or duplicate node identity")
        by_id[node.id] = node
        nodes.append(node)
    edge_ids = set()
    for raw in graph["edges"]:
        edge = Edge.model_validate(raw)
        if edge.project_id != project_id or raw.get("id") != edge.id or edge.id in edge_ids:
            raise ValueError("invalid or duplicate edge identity")
        if edge.src not in by_id or edge.dst not in by_id:
            raise ValueError("edge endpoint is missing from project")
        edge_ids.add(edge.id)
        edges.append(edge)
    for node in nodes:
        required = {}
        if node.type == NodeType.SOURCE_SNAPSHOT:
            required["source_id"] = NodeType.SOURCE
        if node.type == NodeType.EVIDENCE_LINK:
            required["snapshot_id"] = NodeType.SOURCE_SNAPSHOT
        optional = {"review_decision_id": NodeType.DECISION} if node.type == NodeType.CLAIM else {}
        if node.type == NodeType.RUN:
            optional.update(transformation_id=NodeType.TRANSFORMATION, replay_of=NodeType.RUN)
        for key, kind in {**required, **optional}.items():
            target = node.data.get(key)
            if key in optional and target is None:
                continue
            if not isinstance(target, str) or target not in by_id or by_id[target].type != kind:
                raise ValueError("node metadata references a missing or wrong-type project node")
    for dataset_id, content in graph["datasets"].items():
        if (
            not isinstance(content, dict)
            or not isinstance(content.get("records"), list)
            or not isinstance(content.get("resolutions"), dict)
        ):
            raise TypeError("invalid dataset collections")
        if dataset_id not in by_id or by_id[dataset_id].type != NodeType.DATASET:
            raise ValueError("records reference an unregistered dataset")
        keys = set()
        for raw in content["records"]:
            record = Record.model_validate(raw)
            if (
                record.project_id != project_id
                or record.dataset_id != dataset_id
                or record.key in keys
            ):
                raise ValueError("invalid or duplicate record identity")
            keys.add(record.key)
            records.append(record)
        for key, raw in content["resolutions"].items():
            resolution = Resolution.model_validate(raw)
            target = by_id.get(resolution.target_id)
            kind = (
                NodeType.DECISION
                if resolution.status == "assumption_backed"
                else NodeType.EVIDENCE_LINK
            )
            if (
                resolution.project_id != project_id
                or resolution.dataset_id != dataset_id
                or key != resolution.record_key
                or key not in keys
                or target is None
                or target.type != kind
            ):
                raise ValueError("invalid resolution relationship")
            resolutions.append(resolution)
    for ref in retained_references(nodes):
        _check_reference(ref)
    return nodes, edges, records, resolutions


def _new_destination(destination: Path) -> Path:
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination must not exist")
    parent = destination.parent.resolve(strict=True)
    if any(
        part in _PROTECTED or part in {".lineage", ".env"} or part.startswith(".env.")
        for part in destination.parts + parent.parts
    ):
        raise ValueError("protected destination")
    return parent / destination.name


def export_project(
    store: Store,
    project_id: str,
    root: Path,
    destination: Path,
    *,
    contracts_path: Path | None = None,
    include_content: bool = False,
    max_blob_bytes: int = MAX_BLOB_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict:
    """Create a new archive inside the project; source bytes require explicit opt-in."""
    _limits(max_blob_bytes, max_total_bytes)
    if type(include_content) is not bool:
        raise TypeError("include_content must be an explicit boolean")
    root = Path(root).resolve(strict=True)
    destination = _new_destination(
        Path(destination) if Path(destination).is_absolute() else root / destination
    )
    if not destination.is_relative_to(root):
        raise ValueError("export destination escapes project root")
    if destination == store.path.resolve() or destination.name.startswith(store.path.name + "-"):
        raise ValueError("protected database destination")
    with store.transaction(write=False):
        graph = export_graph(store, project_id)
    nodes, _, _, _ = _validate_graph(graph)
    refs = retained_references(nodes)
    hashes = sorted({_check_reference(ref) for ref in refs})
    policy = None
    if contracts_path is not None:
        path = Path(contracts_path)
        path = path if path.is_absolute() else root / path
        # Normalize aliases above the trusted root (e.g. macOS /var), while
        # preserving every path component inside it for the no-symlink reader.
        # Outermost first also prevents an internal link back to root from
        # hiding that symlink in the resulting relative path.
        for ancestor in reversed(path.parents):
            if ancestor == root or ancestor.resolve(strict=True) == root:
                relative = path.relative_to(ancestor).as_posix()
                break
        else:
            raise ValueError("policy path escapes project root")
        policy = read_project_file(root, relative)
        parse_contracts(policy)
    manifest = {
        "format": "xyle-trace-handoff",
        "version": 1,
        "project_id": project_id,
        "include_content": include_content,
        "content_complete": include_content,
        "required_blobs": hashes,
        "policy_included": policy is not None,
        "members": {},
    }
    temporary = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".handoff-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:

                def write(name, data):
                    nonlocal total
                    total += len(data)
                    if (
                        len(data) > MAX_MEMBER_BYTES
                        or total > max_total_bytes
                        or len(manifest["members"]) >= MAX_MEMBERS - 1
                    ):
                        raise ValueError("handoff exceeds archive limits")
                    manifest["members"][name] = {
                        "content_hash": hash_bytes(data),
                        "size_bytes": len(data),
                    }
                    archive.writestr(name, data)

                write("graph.json", canonical_json(graph).encode())
                if policy is not None:
                    write("contracts.yaml", policy)
                if include_content:
                    for digest in hashes:
                        storage = root / ".lineage" / "snapshots"
                        if storage.parent.is_symlink() or storage.is_symlink():
                            raise ValueError("unsafe retained content directory")
                        name = "blobs/" + digest.removeprefix("sha256:")
                        if len(manifest["members"]) >= MAX_MEMBERS - 1:
                            raise ValueError("archive member limit exceeded")
                        limit = min(max_blob_bytes, max_total_bytes - total)
                        with (
                            _regular_stream(storage / name.removeprefix("blobs/"), limit) as source,
                            archive.open(name, "w", force_zip64=True) as output,
                        ):
                            info = _copy_verified(source, output, limit, expected=digest)
                        manifest["members"][name] = info
                        total += info["size_bytes"]
                encoded = canonical_json(manifest).encode()
                if len(encoded) > MAX_MEMBER_BYTES or total + len(encoded) > max_total_bytes:
                    raise ValueError("handoff manifest exceeds archive limits")
                archive.writestr("manifest.json", encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-clobber publication, including races with another exporter.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**manifest, "path": str(destination)}


def import_project(
    bundle_path: Path,
    destination: Path,
    *,
    max_blob_bytes: int = MAX_BLOB_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict:
    """Verify then create an isolated project, preserving IDs without executing content."""
    _limits(max_blob_bytes, max_total_bytes)
    destination = _new_destination(destination)
    try:
        # Bound archive bytes before parsing its central directory.
        bundle_path = Path(bundle_path)
        if bundle_path.is_symlink() or not bundle_path.is_file():
            raise ValueError("bundle must be a regular file")
        with _regular_stream(bundle_path, max_total_bytes + MAX_MEMBERS * 1024) as stream:
            _check_directory(stream)
            return _import_archive(stream, destination, max_blob_bytes, max_total_bytes)
    except (KeyError, TypeError, zipfile.BadZipFile, RecursionError, struct.error) as error:
        raise ValueError("invalid handoff archive") from error


def _import_archive(stream, destination, max_blob_bytes, max_total_bytes):
    temporary = None
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) > MAX_MEMBERS or len(set(names)) != len(names):
                raise ValueError("duplicate members or archive member limit exceeded")
            if sum(item.file_size for item in infos) > max_total_bytes:
                raise ValueError("archive expanded byte limit exceeded")
            for item in infos:
                mode = item.external_attr >> 16
                limit = max_blob_bytes if item.filename.startswith("blobs/") else MAX_MEMBER_BYTES
                if (
                    item.file_size > limit
                    or item.flag_bits & 1
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                ):
                    raise ValueError("unsafe archive member or byte limit exceeded")
                if item.filename not in {"manifest.json", "graph.json", "contracts.yaml"} and not (
                    re.fullmatch(r"blobs/[0-9a-f]{64}", item.filename)
                ):
                    raise ValueError("unknown or unsafe archive member")
            if "manifest.json" not in names or "graph.json" not in names:
                raise ValueError("missing handoff metadata")

            def read(name):
                with archive.open(name) as source:
                    data = source.read(MAX_MEMBER_BYTES + 1)
                if len(data) > MAX_MEMBER_BYTES:
                    raise ValueError("archive member exceeds byte limit")
                return data

            manifest = _json(read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != "xyle-trace-handoff"
                or type(manifest.get("version")) is not int
                or manifest["version"] != 1
                or type(manifest.get("include_content")) is not bool
                or type(manifest.get("content_complete")) is not bool
                or manifest["content_complete"] != manifest["include_content"]
                or type(manifest.get("policy_included")) is not bool
                or not isinstance(manifest.get("members"), dict)
                or set(manifest["members"]) != set(names) - {"manifest.json"}
            ):
                raise ValueError("invalid handoff manifest")

            def verified(name):
                data = read(name)
                expected = manifest["members"][name]
                if (
                    not isinstance(expected, dict)
                    or type(expected.get("size_bytes")) is not int
                    or expected.get("size_bytes") != len(data)
                    or expected.get("content_hash") != hash_bytes(data)
                ):
                    raise ValueError("member size or hash mismatch")
                return data

            graph = _json(verified("graph.json"))
            nodes, edges, records, resolutions = _validate_graph(graph)
            hashes = sorted({_check_reference(ref) for ref in retained_references(nodes)})
            if (
                manifest.get("project_id") != graph["project_id"]
                or manifest.get("required_blobs") != hashes
                or manifest["policy_included"] != ("contracts.yaml" in names)
            ):
                raise ValueError("manifest does not match graph relationships")
            expected_blobs = {"blobs/" + h.removeprefix("sha256:") for h in hashes}
            actual_blobs = {name for name in names if name.startswith("blobs/")}
            if actual_blobs != (expected_blobs if manifest["include_content"] else set()):
                raise ValueError("retained members do not match manifest")
            policy = verified("contracts.yaml") if manifest["policy_included"] else None
            if policy is not None:
                parse_contracts(policy)
            temporary = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".handoff-"))
            snapshots = temporary / ".lineage" / "snapshots"
            snapshots.mkdir(parents=True)
            for name in sorted(actual_blobs):
                expected = manifest["members"][name]
                with (
                    archive.open(name) as source,
                    (snapshots / name.removeprefix("blobs/")).open("xb") as output,
                ):
                    actual = _copy_verified(
                        source,
                        output,
                        max_blob_bytes,
                        expected="sha256:" + name.removeprefix("blobs/"),
                    )
                if (
                    not isinstance(expected, dict)
                    or type(expected.get("size_bytes")) is not int
                    or actual != expected
                ):
                    raise ValueError("blob size or hash does not match manifest")
        store = Store(temporary / ".lineage" / "graph.db")
        try:
            with store.transaction():
                for node in nodes:
                    store.upsert_node(node)
                store.put_records(records)
                for resolution in resolutions:
                    store.put_resolution(resolution)
                for edge in edges:
                    store.upsert_edge(edge)
        finally:
            store.close()
        if policy is not None:
            (temporary / "contracts.yaml").write_bytes(policy)
        config = {
            "project": graph["project_id"],
            "db": ".lineage/graph.db",
            "root": ".",
            "contracts": "contracts.yaml" if policy is not None else None,
        }
        (temporary / ".lineage" / "config.json").write_text(canonical_json(config) + "\n")
        # The local destination parent must not be concurrently controlled by an adversary.
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination appeared during import")
        os.rename(temporary, destination)
        temporary = None
        return {
            "project_id": graph["project_id"],
            "root": str(destination),
            "database_path": str(destination / ".lineage" / "graph.db"),
            "content_complete": manifest["content_complete"],
            "policy_included": manifest["policy_included"],
        }
    except (KeyError, TypeError, zipfile.BadZipFile, RecursionError) as error:
        raise ValueError("invalid handoff archive") from error
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
