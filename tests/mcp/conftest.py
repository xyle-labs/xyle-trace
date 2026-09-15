import pytest

from xyle_trace.models.entities import Node, NodeType
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def config(tmp_path):
    from xyle_trace.mcp.server import ServerConfig

    db = tmp_path / "graph.db"
    store = Store(db)
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    store.close()
    policies = tmp_path / "contracts.yaml"
    policies.write_text("[]\n")
    return ServerConfig("p", db, tmp_path, policies)
