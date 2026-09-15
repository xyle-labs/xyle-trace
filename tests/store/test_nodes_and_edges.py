import pytest

from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.db")
    yield s
    s.close()


def test_upsert_then_get_node_round_trips(store):
    node = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa", data={"org": "PSA"})
    store.upsert_node(node)
    loaded = store.get_node("p", node.id)
    assert loaded is not None
    assert loaded.data["org"] == "PSA"


def test_upsert_is_idempotent_on_natural_key(store):
    first = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa", data={"v": 1})
    store.upsert_node(first)
    store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="psa", data={"v": 2}))
    assert len(store.nodes_of_type("p", NodeType.SOURCE)) == 1
    assert store.get_node("p", first.id).data["v"] == 2


def test_get_node_is_scoped_to_project(store):
    node = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa")
    store.upsert_node(node)
    assert store.get_node("other", node.id) is None


def test_nodes_of_type_is_scoped_to_project(store):
    store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="a"))
    store.upsert_node(Node(project_id="q", type=NodeType.SOURCE, natural_key="b"))
    assert len(store.nodes_of_type("p", NodeType.SOURCE)) == 1


def test_edges_from_returns_outgoing_edges(store):
    edge = Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="dataset:a", dst="dataset:b")
    store.upsert_edge(edge)
    assert [e.dst for e in store.edges_from("p", "dataset:a")] == ["dataset:b"]
    assert store.edges_from("p", "dataset:b") == []


def test_edges_to_returns_incoming_edges(store):
    store.upsert_edge(Edge(project_id="p", type=EdgeType.PRODUCED, src="run:r", dst="artifact:f"))
    assert [e.src for e in store.edges_to("p", "artifact:f")] == ["run:r"]


def test_edges_can_be_filtered_by_type(store):
    store.upsert_edge(Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="a", dst="b"))
    store.upsert_edge(Edge(project_id="p", type=EdgeType.DEPENDS_ON, src="a", dst="c"))
    filtered = store.edges_from("p", "a", types=[EdgeType.DERIVED_FROM])
    assert [e.dst for e in filtered] == ["b"]


def test_edge_upsert_is_idempotent(store):
    for _ in range(3):
        store.upsert_edge(Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="a", dst="b"))
    assert len(store.edges_from("p", "a")) == 1


def test_store_reopens_existing_database(tmp_path):
    path = tmp_path / "graph.db"
    first = Store(path)
    node = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa")
    first.upsert_node(node)
    first.close()

    second = Store(path)
    assert second.get_node("p", node.id) is not None
    second.close()
