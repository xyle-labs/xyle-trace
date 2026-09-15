import pytest

from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType
from xyle_trace.queries.traversal import downstream, explain, impact, upstream
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def chain(tmp_path):
    """source <- snapshot <- dataset <- run -> artifact <- evidence -> claim"""
    store = Store(tmp_path / "graph.db")
    ids = {}
    for key, node_type in [
        ("source", NodeType.SOURCE),
        ("snapshot", NodeType.SOURCE_SNAPSHOT),
        ("dataset", NodeType.DATASET),
        ("run", NodeType.RUN),
        ("artifact", NodeType.ARTIFACT),
        ("evidence", NodeType.EVIDENCE_LINK),
        ("claim", NodeType.CLAIM),
    ]:
        node = Node(project_id="p", type=node_type, natural_key=key)
        store.upsert_node(node)
        ids[key] = node.id

    for edge_type, src, dst in [
        (EdgeType.SNAPSHOT_OF, "snapshot", "source"),
        (EdgeType.EXTRACTED_FROM, "dataset", "snapshot"),
        (EdgeType.USED_INPUT, "run", "dataset"),
        (EdgeType.PRODUCED, "run", "artifact"),
        (EdgeType.SUPPORTS, "evidence", "claim"),
        (EdgeType.DERIVED_FROM, "evidence", "artifact"),
    ]:
        store.upsert_edge(Edge(project_id="p", type=edge_type, src=ids[src], dst=ids[dst]))

    yield store, ids
    store.close()


def test_upstream_from_artifact_reaches_the_original_source(chain):
    store, ids = chain
    assert ids["source"] in upstream(store, "p", ids["artifact"])


def test_upstream_walks_produced_edges_backwards(chain):
    store, ids = chain
    assert ids["run"] in upstream(store, "p", ids["artifact"])


def test_upstream_excludes_the_starting_node(chain):
    store, ids = chain
    assert ids["artifact"] not in upstream(store, "p", ids["artifact"])


def test_upstream_respects_max_depth(chain):
    store, ids = chain
    assert upstream(store, "p", ids["artifact"], max_depth=1) == [ids["run"]]


def test_downstream_from_source_reaches_the_claim(chain):
    store, ids = chain
    assert ids["claim"] in downstream(store, "p", ids["source"])


def test_traversal_is_scoped_to_project(chain):
    store, ids = chain
    assert upstream(store, "other", ids["artifact"]) == []


def test_traversal_terminates_on_a_cycle(tmp_path):
    store = Store(tmp_path / "cycle.db")
    a = Node(project_id="p", type=NodeType.DATASET, natural_key="a")
    b = Node(project_id="p", type=NodeType.DATASET, natural_key="b")
    store.upsert_node(a)
    store.upsert_node(b)
    store.upsert_edge(Edge(project_id="p", type=EdgeType.DERIVED_FROM, src=a.id, dst=b.id))
    store.upsert_edge(Edge(project_id="p", type=EdgeType.DERIVED_FROM, src=b.id, dst=a.id))
    assert upstream(store, "p", a.id) == [b.id]
    store.close()


def test_explain_lists_the_originating_sources(chain):
    store, ids = chain
    result = explain(store, "p", ids["artifact"])
    assert result["node"] == ids["artifact"]
    assert result["sources"] == [ids["source"]]


def test_impact_flags_downstream_claims_for_review(chain):
    store, ids = chain
    result = impact(store, "p", ids["source"])
    assert result["claims_needing_review"] == [ids["claim"]]
    assert ids["artifact"] in result["affected"]
