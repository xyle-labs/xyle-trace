"""Derived answers to two standing questions about a project's honesty.

Which assumptions are still holding the graph up? And which runs have
actually been shown to reproduce? Both are computed from what is already
recorded; neither adds a node or edge type.

The graph records which assumptions currently back which records, and
nothing more. Each assumption entry carries a `status`:

- `"standing"` — `backs` is non-empty: this assumption is currently the
  backing for at least one record, right now.
- `"unbacked"` — `backs` is empty: this assumption backs no record today.

An `"unbacked"` assumption may have been superseded by better evidence, or it
may never have been bound to a record in the first place. The graph cannot
tell those two apart: `Store.put_records` deletes a record's resolution row
whenever a correction rewrites that record's value (see
`store/sqlite_store.py`'s `put_records`), so the very link between the old
assumption and the record it used to back is gone by the time anything asks.
Nothing records which correction, if any, did the superseding. Distinguishing
"superseded" from "never used" would require a correction to record what it
superseded — a real capability this model does not have. That is a separate
architectural decision, not something to fake here: no node type, edge type,
or persisted field is added to close the gap, and `unbacked` stays honestly
`unbacked` rather than guessing.
"""

from xyle_trace.models.entities import NodeType
from xyle_trace.store.sqlite_store import Store


def standing_assumptions(store: Store, project_id: str) -> list[dict]:
    backing: dict[str, list[dict]] = {}
    for dataset_id in store.dataset_ids(project_id):
        for key, resolution in store.resolutions(project_id, dataset_id, resolve=False).items():
            if resolution.status == "assumption_backed":
                backing.setdefault(resolution.target_id, []).append(
                    {"dataset_id": dataset_id, "record_key": key}
                )

    entries = []
    for node in store.nodes_of_type(project_id, NodeType.DECISION):
        if node.data.get("kind") != "assumption":
            continue
        backs = backing.get(node.id, [])
        entries.append(
            {
                "decision_id": node.id,
                "natural_key": node.natural_key,
                "assumption_type": node.data.get("assumption_type"),
                "backs": backs,
                "status": "standing" if backs else "unbacked",
                "replacement_action": (node.data.get("metadata") or {}).get("replacement_action"),
            }
        )
    return entries


def replay_verification(store: Store, project_id: str) -> dict[str, list[str]]:
    verified: dict[str, list[str]] = {}
    for node in store.nodes_of_type(project_id, NodeType.RUN):
        original = node.data.get("replay_of")
        if original and node.data.get("replay_verified") is True:
            verified.setdefault(original, []).append(node.id)
    return {key: sorted(value) for key, value in verified.items()}
