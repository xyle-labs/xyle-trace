from collections import deque

from xyle_trace.models.entities import EDGE_DIRECTION, EdgeType, NodeType
from xyle_trace.store.sqlite_store import Store

_UPSTREAM_EDGES = [t for t, d in EDGE_DIRECTION.items() if d == "upstream"]
_DOWNSTREAM_EDGES = [t for t, d in EDGE_DIRECTION.items() if d == "downstream"]


def _dependencies(store: Store, project_id: str) -> list[tuple[str, str]]:
    """Explicit metadata references and row resolutions act as dependency edges."""
    dependencies = store.resolution_dependencies(project_id)
    nodes = {node.id: node for node in store.all_nodes(project_id)}
    references = {
        NodeType.EVIDENCE_LINK: ("snapshot_id", NodeType.SOURCE_SNAPSHOT),
        NodeType.SOURCE_SNAPSHOT: ("source_id", NodeType.SOURCE),
    }
    for node in nodes.values():
        if node.type not in references:
            continue
        field, expected_type = references[node.type]
        target_id = node.data.get(field)
        if isinstance(target_id, str):
            target_id = target_id.strip()
        if (
            isinstance(target_id, str)
            and target_id in nodes
            and nodes[target_id].type == expected_type
        ):
            dependencies.append((node.id, target_id))
    return dependencies


def _walk(
    store: Store,
    project_id: str,
    node_id: str,
    forward: list[EdgeType],
    backward: list[EdgeType],
    max_depth: int | None,
) -> list[str]:
    """Breadth-first walk in one lineage direction.

    `forward` edge types are followed src to dst; `backward` types are followed
    dst to src. Lateral types (SUPERSEDES, REVIEWED_BY) are in neither list and
    are never traversed.
    """
    seen = {node_id}
    implicit: dict[str, list[str]] = {}
    for consumer, dependency in _dependencies(store, project_id):
        src, dst = (consumer, dependency) if forward == _UPSTREAM_EDGES else (dependency, consumer)
        implicit.setdefault(src, []).append(dst)
    order: list[str] = []
    queue = deque([(node_id, 0)])
    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        neighbours = [e.dst for e in store.edges_from(project_id, current, forward)]
        neighbours += [e.src for e in store.edges_to(project_id, current, backward)]
        neighbours += sorted(implicit.get(current, []))
        for neighbour in neighbours:
            if neighbour in seen:
                continue
            seen.add(neighbour)
            order.append(neighbour)
            queue.append((neighbour, depth + 1))
    return order


def upstream(
    store: Store, project_id: str, node_id: str, max_depth: int | None = None
) -> list[str]:
    return _walk(store, project_id, node_id, _UPSTREAM_EDGES, _DOWNSTREAM_EDGES, max_depth)


def downstream(
    store: Store, project_id: str, node_id: str, max_depth: int | None = None
) -> list[str]:
    return _walk(store, project_id, node_id, _DOWNSTREAM_EDGES, _UPSTREAM_EDGES, max_depth)


def explain(store: Store, project_id: str, node_id: str) -> dict:
    ancestors = upstream(store, project_id, node_id)
    sources = [
        node_id_
        for node_id_ in ancestors
        if (node := store.get_node(project_id, node_id_)) and node.type == NodeType.SOURCE
    ]
    included = {node_id, *ancestors}
    return {
        "node": node_id,
        "upstream": ancestors,
        "sources": sources,
        **subgraph(store, project_id, included),
    }


def subgraph(store: Store, project_id: str, included: set[str]) -> dict:
    """Retain full metadata and implicit dependencies among the selected nodes."""
    return {
        "nodes": [
            n.model_dump(mode="json") for n in store.all_nodes(project_id) if n.id in included
        ],
        "edges": [
            e.model_dump(mode="json")
            for e in store.all_edges(project_id)
            if e.src in included and e.dst in included
        ],
        "dependencies": [
            {"consumer": src, "dependency": dst}
            for src, dst in _dependencies(store, project_id)
            if src in included and dst in included
        ],
    }


def impact(store: Store, project_id: str, node_id: str) -> dict:
    affected = downstream(store, project_id, node_id)
    claims = [
        node_id_
        for node_id_ in affected
        if (node := store.get_node(project_id, node_id_)) and node.type == NodeType.CLAIM
    ]
    return {"affected": affected, "claims_needing_review": claims}
