from xyle_trace.queries.ledger import replay_verification, standing_assumptions
from xyle_trace.store.sqlite_store import Store


def export_graph(store: Store, project_id: str) -> dict:
    datasets = {}
    for dataset_id in store.dataset_ids(project_id):
        datasets[dataset_id] = {
            "records": [
                r.model_dump(mode="json")
                for r in store.records(project_id, dataset_id, resolve=False)
            ],
            "resolutions": {
                key: value.model_dump(mode="json")
                for key, value in store.resolutions(project_id, dataset_id, resolve=False).items()
            },
        }
    return {
        "project_id": project_id,
        "nodes": [n.model_dump(mode="json") for n in store.all_nodes(project_id)],
        "edges": [e.model_dump(mode="json") for e in store.all_edges(project_id)],
        "datasets": datasets,
        "assumptions": standing_assumptions(store, project_id),
        "replay_verification": replay_verification(store, project_id),
    }
