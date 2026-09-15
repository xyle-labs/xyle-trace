import pytest

from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.db")
    source = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa")
    dataset = Node(project_id="p", type=NodeType.DATASET, natural_key="manifest")
    evidence = Node(project_id="p", type=NodeType.EVIDENCE_LINK, natural_key="ev-1")
    s.upsert_node(source)
    s.upsert_node(dataset)
    s.upsert_node(evidence)
    s.upsert_edge(Edge(project_id="p", type=EdgeType.EXTRACTED_FROM, src=dataset.id, dst=source.id))
    s.put_records([Record(project_id="p", dataset_id=dataset.id, key="row0")])
    s.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset.id,
            record_key="row0",
            status="evidence_backed",
            target_id=evidence.id,
        )
    )
    yield s, dataset.id
    s.close()


def test_export_contains_nodes_and_edges(store):
    s, _ = store
    graph = export_graph(s, "p")
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 1


def test_export_contains_dataset_records_and_resolutions(store):
    s, dataset_id = store
    graph = export_graph(s, "p")
    dataset = graph["datasets"][dataset_id]
    assert [r["key"] for r in dataset["records"]] == ["row0"]
    assert dataset["resolutions"]["row0"]["status"] == "evidence_backed"


def test_export_is_scoped_to_project(store):
    s, _ = store
    assert export_graph(s, "other")["nodes"] == []


def test_export_is_json_serialisable(store):
    import json

    s, _ = store
    json.dumps(export_graph(s, "p"))
