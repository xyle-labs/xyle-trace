"""Result explanations with an explicit boundary between current and retained data."""

import json
import re
from pathlib import Path

from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.revisions import revision
from xyle_trace.core.snapshots import read_snapshot
from xyle_trace.models import operations as op
from xyle_trace.models.entities import EDGE_DIRECTION, EdgeType, Node, NodeType
from xyle_trace.queries.traversal import _decision_subjects, explain


class ExplanationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _json_blob(root, reference):
    if root is None or not isinstance(reference, dict):
        raise ValueError("retained content is unavailable")
    digest = reference.get("content_hash", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid retained content hash")
    if reference.get("path") != f".lineage/snapshots/{digest[7:]}":
        raise ValueError("invalid retained content path")
    try:
        payload = read_snapshot(root / ".lineage/snapshots", digest)
    except (OSError, ValueError) as error:
        raise ValueError("retained content is unavailable or unsafe") from error
    if hash_bytes(payload) != digest:
        raise ValueError("retained content hash differs")
    value = json.loads(payload, parse_constant=lambda value: _bad_number(value))
    revision(value)  # Reject overflow such as 1e999 as well as explicit nonfinite tokens.
    return value


def _validate_bundle(bundle):
    if (
        not isinstance(bundle, dict)
        or bundle.get("version") != 1
        or not isinstance(bundle.get("code"), str)
        or not isinstance(bundle.get("transformation_id"), str)
        or not isinstance(bundle.get("input"), dict)
        or not isinstance(bundle["input"].get("params"), dict)
        or not isinstance(bundle["input"].get("inputs"), dict)
    ):
        raise ValueError("unsupported retained bundle")
    for node_id, entry in bundle["input"]["inputs"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("node"), dict):
            raise TypeError("invalid retained input")
        node = Node.model_validate(entry["node"])
        if node.id != node_id or entry["node"].get("id") != node_id:
            raise ValueError("retained input identity differs")
        if not isinstance(entry.get("records"), list) or not isinstance(entry.get("lineage"), dict):
            raise TypeError("retained input context is missing")
        for row in entry["records"]:
            state = op.RecordState.model_validate(row)
            if state.record.dataset_id != node_id:
                raise ValueError("retained row belongs to another input")
        for key, model, field in (("nodes", op.NodeState, "node"), ("edges", op.EdgeState, "edge")):
            items = entry["lineage"].get(key)
            if not isinstance(items, list):
                raise TypeError("retained lineage is missing")
            for item in items:
                parsed = model.model_validate(item)
                if getattr(parsed, field).project_id != node.project_id:
                    raise ValueError("retained lineage belongs to another project")
        dependencies = entry["lineage"].get("dependencies")
        if not isinstance(dependencies, list):
            raise TypeError("retained dependencies are missing")
        for item in dependencies:
            op.Dependency.model_validate(item)


def _positive_pairs(graph):
    pairs = [(item["consumer"], item["dependency"]) for item in graph["dependencies"]]
    for edge in graph["edges"]:
        if edge["type"] == "contradicts" or edge["data"].get("provisional", False):
            continue
        direction = EDGE_DIRECTION.get(edge["type"])
        if direction == "upstream":
            pairs.append((edge["src"], edge["dst"]))
        elif direction == "downstream":
            pairs.append((edge["dst"], edge["src"]))
    return pairs


def _positive_ancestors(graph, node_id):
    pairs = _positive_pairs(graph)
    seen = {node_id}
    queue = [node_id]
    for current in queue:
        for consumer, dependency in pairs:
            if consumer == current and dependency not in seen:
                seen.add(dependency)
                queue.append(dependency)
    return seen


def _selection_context(graph, node_id, run_id, ancestors):
    """Current connectors to a selected producer, excluding its upstream inputs."""
    consumers = {}
    for consumer, dependency in _positive_pairs(graph):
        consumers.setdefault(dependency, []).append(consumer)
    seen = {run_id}
    queue = [run_id]
    for current in queue:
        if current == node_id:
            continue
        for consumer in consumers.get(current, []):
            if consumer in ancestors and consumer not in seen:
                seen.add(consumer)
                queue.append(consumer)
    return seen


def _bind_bundle(bundle, run, edges, original):
    """Reject retained context inconsistent with the stored execution receipt."""
    related = [edge for edge in edges if not edge.data.get("provisional", False)]
    transformations = {edge.dst for edge in related if edge.type == EdgeType.GENERATED_BY}
    inputs = {edge.dst for edge in related if edge.type == EdgeType.USED_INPUT}
    if (transformations != {bundle["transformation_id"]}
            or inputs != set(bundle["input"]["inputs"])):
        raise ValueError("retained bundle differs from run relationships")
    if any(entry["node"]["project_id"] != run.project_id
           for entry in bundle["input"]["inputs"].values()):
        raise ValueError("retained input belongs to another project")
    for field in ("policy_hash", "environment"):
        if field not in bundle or field not in run.data or bundle[field] != run.data[field]:
            raise ValueError("retained bundle differs from run receipt")
    request = run.data.get("execution_request")
    if not isinstance(request, dict):
        raise TypeError("managed execution request is unavailable")
    if run.data.get("replay_of"):
        if (original is None or original.type != NodeType.RUN
                or original.data.get("capture_mode") != "managed"
                or original.data.get("status") != "succeeded"
                or original.data.get("bundle") != run.data.get("bundle")
                or request.get("replay_of") != original.id):
            raise ValueError("retained replay bundle differs from original receipt")
    elif (request.get("transformation_id") != bundle["transformation_id"]
            or set(request.get("input_ids") or []) != inputs
            or (request.get("params") or {}) != bundle["input"]["params"]):
        raise ValueError("retained bundle differs from execution request")


def _bad_number(value):
    raise ValueError(f"nonfinite JSON value: {value}")


def _select(value, pointer):
    if pointer is None or pointer == "":
        return value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ExplanationError("invalid_request", "selector must be a JSON pointer")
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", part):
                    raise KeyError(part)
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError):
            raise ExplanationError("not_found", "selected field is absent from recorded output")
    return value


def _related_decisions(store, project_id, nodes, included):
    result = []
    for node in nodes:
        if node.type != NodeType.DECISION:
            continue
        subjects = set(_decision_subjects(store, project_id, node))
        if node.id in included or subjects.intersection(included):
            result.append(
                {
                    "id": node.id,
                    "kind": node.data.get("kind", "assumption"),
                    "rationale": node.data.get("rationale"),
                    "revision": revision(node.data),
                    "data": node.data,
                }
            )
    return result


def _working_output_matches(node, run):
    output = run.data.get("output")
    working = run.data.get("working_output")
    hashes = run.data.get("output_file_hashes")
    return (
        run.data.get("capture_mode") == "managed"
        and run.data.get("status") == "succeeded"
        and isinstance(output, dict)
        and isinstance(working, dict)
        and working.get("node_id") == node.id
        and working.get("path") == node.data.get("path")
        and working.get("content_hash") == node.data.get("content_hash")
        and working.get("content_hash") == output.get("content_hash")
        and output.get("content_hash") is not None
        and isinstance(hashes, dict)
        and hashes.get(node.id) == working.get("content_hash")
    )


def _managed_output_matches(node, run):
    output = run.data.get("output")
    return isinstance(output, dict) and (
        all(node.data.get(field) == output.get(field) for field in ("path", "content_hash"))
        or _working_output_matches(node, run)
    )


def explain_result(
    store,
    project_id,
    node,
    *,
    root=None,
    run_id=None,
    expected_revision=None,
    selector=None,
    include_code=False,
):
    """Return (readable explanation, optional retained graph).

    A current graph is context only for a reported run. For managed execution the
    retained bundle supplies the historical input rows, resolutions and lineage.
    Code is returned only when explicitly requested; source bytes are never read.
    """
    if expected_revision is not None and expected_revision != revision(node.data):
        raise ExplanationError("conflict", "selected node revision has changed")
    if (
        selector is not None
        and selector != ""
        and (not selector.startswith("/") or re.search(r"~(?![01])", selector))
    ):
        raise ExplanationError("invalid_request", "selector must be a JSON pointer")
    graph = explain(store, project_id, node.id)
    all_nodes = store.all_nodes(project_id)
    by_id = {item.id: item for item in all_nodes}
    included = {item["id"] for item in graph["nodes"]}
    positive = _positive_ancestors(graph, node.id)
    candidates = [by_id[key] for key in positive if by_id[key].type == NodeType.RUN]
    direct = [
        by_id[edge.src]
        for edge in store.edges_to(project_id, node.id, [EdgeType.PRODUCED])
        if edge.src in by_id
        and by_id[edge.src].type == NodeType.RUN
        and not edge.data.get("provisional", False)
    ]
    gaps = []
    if node.type == NodeType.ARTIFACT:
        candidates = direct
    elif node.type == NodeType.RUN:
        candidates = [node]
    current_working = []
    if node.type == NodeType.ARTIFACT and direct and all(
        run.data.get("capture_mode") == "managed"
        and isinstance(run.data.get("working_output"), dict)
        and run.data["working_output"].get("node_id") == node.id
        for run in direct
    ):
        current_working = [run for run in direct if _working_output_matches(node, run)]
    if run_id is not None:
        if run_id not in {run.id for run in candidates}:
            raise ExplanationError("invalid_reference", "run did not produce this result")
        run = by_id[run_id]
    elif node.type == NodeType.RUN:
        run = node
    elif len(direct) == 1:
        run = direct[0]
    elif len(current_working) == 1:
        run = current_working[0]
    elif len(candidates) == 1:
        run = candidates[0]
    else:
        run = None
        gaps.append(
            {
                "kind": "ambiguous_producer" if candidates else "producer_unrecorded",
                "message": "Select a producing run"
                if candidates
                else "No producing run is recorded",
            }
        )
    historical = None
    inputs = []
    calculations = []
    bundle = None
    value = None
    value_available = False
    state = "unrecorded"
    if run:
        mode = run.data.get("capture_mode", "reported")
        replayed = (
            mode == "managed"
            and run.data.get("status") == "succeeded"
            and run.data.get("replay_verified") is True
            and run.data.get("reproducible") is True
        )
        state = (
            "failed"
            if run.data.get("status") == "failed"
            else "replayed"
            if replayed
            else "executed"
            if mode == "managed" and run.data.get("status") == "succeeded"
            else "started"
            if mode == "managed" and run.data.get("status") == "started"
            else "unrecorded"
            if mode == "managed"
            else "reported"
        )
        if mode == "managed":
            try:
                bundle = _json_blob(root, run.data.get("bundle"))
                try:
                    _validate_bundle(bundle)
                    _bind_bundle(
                        bundle, run,
                        store.edges_from(project_id, run.id,
                                         [EdgeType.GENERATED_BY, EdgeType.USED_INPUT]),
                        by_id.get(run.data.get("replay_of")),
                    )
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError("retained bundle structure is invalid") from error
            except (OSError, ValueError, KeyError, TypeError) as error:
                bundle = None
                gaps.append({"kind": "retained_inputs_unavailable", "message": str(error)})
        if bundle:
            retained_nodes = {}
            retained_edges = {}
            retained_dependencies = {}
            for input_id, entry in bundle["input"]["inputs"].items():
                inputs.append(
                    {
                        "node_id": input_id,
                        "node": entry["node"],
                        "records": entry.get("records", []),
                        "basis": "retained",
                    }
                )
                current = {
                    row.key: row.data for row in store.records(project_id, input_id, resolve=False)
                }
                prior = {
                    row["record"]["key"]: row["record"]["data"] for row in entry.get("records", [])
                }
                if entry["node"]["type"] == "dataset" and current != prior:
                    gaps.append(
                        {
                            "kind": "current_inputs_differ",
                            "node_id": input_id,
                            "message": "Current rows differ from retained execution inputs",
                        }
                    )
                lineage = entry.get("lineage", {})
                for item in lineage.get("nodes", []):
                    retained_nodes[item["node"]["id"]] = item["node"]
                for item in lineage.get("edges", []):
                    retained_edges[item["edge"]["id"]] = item["edge"]
                for item in lineage.get("dependencies", []):
                    retained_dependencies[(item["consumer"], item["dependency"])] = item
            retained_nodes[run.id] = run.model_dump(mode="json")
            selected = node.model_dump(mode="json")
            retained_nodes[node.id] = selected
            connectors = _selection_context(graph, node.id, run.id, positive)
            for item in graph["nodes"]:
                if item["id"] in connectors:
                    retained_nodes.setdefault(item["id"], item)
            connector_edges = [
                edge for edge in graph["edges"]
                if edge["src"] in connectors and edge["dst"] in connectors
                and edge["type"] != "contradicts"
                and not edge["data"].get("provisional", False)
                and EDGE_DIRECTION.get(edge["type"]) is not None
            ]
            for edge in connector_edges:
                retained_edges.setdefault(edge["id"], edge)
            for pair in graph["dependencies"]:
                if pair["consumer"] in connectors and pair["dependency"] in connectors:
                    retained_dependencies.setdefault((pair["consumer"], pair["dependency"]), pair)
            if connectors - {node.id, run.id}:
                gaps.append({
                    "kind": "current_selection_context",
                    "message": "The selected result's path to this run is current graph context; "
                               "only the run's input provenance is retained historical context",
                    "node_ids": sorted(connectors - {run.id}),
                    "edge_ids": sorted(edge["id"] for edge in connector_edges),
                })
            gaps.append({
                "kind": "transformation_metadata_unretained",
                "node_id": bundle["transformation_id"],
                "message": "Historical transformation metadata was not retained; "
                           "the calculation uses the retained code and parameters",
            })
            historical = {
                "nodes": list(retained_nodes.values()),
                "edges": list(retained_edges.values()),
                "dependencies": list(retained_dependencies.values()),
            }
            for edge in graph["edges"]:
                if (
                    edge["src"] in retained_nodes
                    and edge["dst"] in retained_nodes
                    and edge["type"] in {"produced", "used_input"}
                    and edge["id"] not in retained_edges
                ):
                    historical["edges"].append(edge)
            shown_edges = {edge["id"] for edge in historical["edges"]}
            omitted = []
            for edge in graph["edges"]:
                direction = EDGE_DIRECTION.get(edge["type"])
                consumer = edge["src"] if direction == "upstream" else edge["dst"]
                if (direction is not None and consumer in connectors - {run.id}
                        and edge["id"] not in shown_edges):
                    omitted.append(edge)
            if omitted:
                gaps.append({
                    "kind": "current_result_relationships_omitted",
                    "message": "Additional current result relationships, including any listed "
                               "contradictions or provisional links, are not retained historical "
                               "inputs. Inspect the current upstream graph for their context",
                    "node_ids": sorted({key for edge in omitted
                                        for key in (edge["src"], edge["dst"])}),
                    "edge_ids": sorted(edge["id"] for edge in omitted),
                    "relationship_types": sorted({edge["type"] for edge in omitted}),
                    "provisional_edge_ids": sorted(edge["id"] for edge in omitted
                                                   if edge["data"].get("provisional", False)),
                })
            if run.data.get("output") and (
                node.type == NodeType.RUN or any(item.id == run.id for item in direct)
            ):
                try:
                    recorded = _json_blob(root, run.data["output"])
                    if node.type == NodeType.ARTIFACT and not _managed_output_matches(node, run):
                        gaps.append({"kind": "output_reference_mismatch",
                                     "message": "Selected artifact differs from retained run output"})
                    else:
                        value = _select(recorded, selector)
                        value_available = True
                except (OSError, ValueError, KeyError, TypeError) as error:
                    if isinstance(error, ExplanationError):
                        raise
                    gaps.append({"kind": "output_unavailable", "message": str(error)})
        else:
            request = run.data.get("start_request", {})
            input_ids = request.get("input_ids", [])
            for input_id in input_ids:
                if input_id in by_id:
                    inputs.append(
                        {
                            "node_id": input_id,
                            "node": by_id[input_id].model_dump(mode="json"),
                            "records": [],
                            "basis": "current_context",
                        }
                    )
            gaps.append(
                {
                    "kind": "retained_inputs_unavailable",
                    "message": "Exact historical input contents are unavailable",
                }
            )
            if selector is not None and node.type == NodeType.ARTIFACT:
                expected_hash = run.data.get("output_file_hashes", {}).get(node.id)
                path = node.data.get("path")
                try:
                    candidate = root / path if root is not None and path else None
                    if (
                        candidate is None
                        or Path(path).is_absolute()
                        or candidate.is_symlink()
                        or not candidate.resolve().is_relative_to(root)
                        or not candidate.is_file()
                    ):
                        raise ValueError("output file unavailable or unsafe")
                    if expected_hash is None:
                        raise ValueError("no recorded output hash")
                    if candidate.stat().st_size > 25 * 1024 * 1024:
                        raise ValueError("output exceeds inspection limit")
                    with candidate.open("rb") as stream:
                        payload = stream.read(25 * 1024 * 1024 + 1)
                    if len(payload) > 25 * 1024 * 1024:
                        raise ValueError("output exceeds inspection limit")
                    if hash_bytes(payload) != expected_hash:
                        gaps.append(
                            {
                                "kind": "output_hash_mismatch",
                                "message": "Current output differs from recorded run",
                            }
                        )
                    else:
                        recorded = json.loads(payload, parse_constant=_bad_number)
                        revision(recorded)
                        value = _select(recorded, selector)
                        value_available = True
                except (OSError, ValueError, TypeError) as error:
                    if isinstance(error, ExplanationError):
                        raise
                    gaps.append({"kind": "output_unavailable", "message": str(error)})
        request = run.data.get("start_request") or run.data.get("execution_request", {})
        transformation_id = (
            bundle["transformation_id"] if bundle else request.get("transformation_id")
        )
        transformation = by_id.get(transformation_id)
        calculation = {
            "run_id": run.id,
            "transformation_id": transformation_id,
            "code_reference": (request.get("code_reference") or request.get("script")),
            "parameters": bundle["input"].get("params", {})
            if bundle
            else request.get("params", {}),
            "capture_mode": mode,
            "status": run.data.get("status"),
            "replay_verified": replayed,
        }
        if not calculation["code_reference"] and transformation and not bundle:
            calculation["code_reference"] = transformation.data.get("code_reference")
        if bundle:
            calculation["code_hash"] = hash_bytes(bundle["code"].encode("utf-8"))
            if include_code:
                calculation["code"] = bundle["code"]
        calculations.append(calculation)
        for gap in run.data.get("capture_gaps", []):
            gaps.append({"kind": "capture_gap", "message": gap})
    if (
        not value_available
        and selector is None
        and (run is None or run.data.get("capture_mode") != "managed")
    ):
        metadata = node.data.get("metadata")
        if "value" in node.data:
            value = node.data["value"]
            value_available = True
        elif isinstance(metadata, dict) and "value" in metadata:
            value = metadata["value"]
            value_available = True
    context_nodes = historical["nodes"] if historical else graph["nodes"]
    evidence = []
    assumptions = []
    for item in context_nodes:
        if item["type"] == "evidence_link":
            evidence.append({"id": item["id"], **item["data"]})
        if item["type"] == "decision" and item["data"].get("kind", "assumption") == "assumption":
            assumptions.append({"id": item["id"], **item["data"]})
    if any(edge["data"].get("provisional", False) for edge in graph["edges"]):
        gaps.append(
            {
                "kind": "provisional_relationship",
                "message": "Some reachable relationships are provisional",
            }
        )
    decisions = (
        _related_decisions(store, project_id, all_nodes, included)
        if not historical
        else [
            {"id": item["id"], **item["data"]}
            for item in context_nodes
            if item["type"] == "decision"
        ]
    )
    output = {
        "selection": {
            "node_id": node.id,
            "revision": revision(node.data),
            "run_id": run.id if run else None,
            "selector": selector,
            "historical": bundle is not None,
            "value": value,
            "value_available": value_available,
        },
        "calculations": calculations,
        "inputs": inputs,
        "evidence": evidence,
        "assumptions": assumptions,
        "decisions": decisions,
        "gaps": gaps,
        "execution": {
            "state": state,
            "status": run.data.get("status") if run else None,
            "reproducible": state == "replayed",
        },
        "graph_basis": "retained_inputs" if historical else "current_context",
    }
    return output, historical
