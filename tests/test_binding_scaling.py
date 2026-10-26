from contextlib import closing

import pytest

from xyle_trace.models.entities import Edge, EdgeType, Node, NodeType
from xyle_trace.service import Service
from xyle_trace.store.sqlite_store import Store


def test_binding_batch_does_not_reload_whole_tables_for_each_record(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "graph.db")) as store:
        store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
        service = Service(store, "p", contracts=[], root=tmp_path)
        rows = service.register(
            {
                "type": "dataset",
                "natural_key": "inputs",
                "records": [{"key": str(i), "data": {"value": i}} for i in range(40)],
            }
        )
        counts = {"records": 0, "resolutions": 0}
        for name in counts:
            original = getattr(store, name)

            def counted(*args, _name=name, _original=original, **kwargs):
                counts[_name] += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(store, name, counted)
        result = service.decide(
            {
                "kind": "assumption",
                "natural_key": "proxy",
                "assumption_type": "test",
                "rationale": "Synthetic fixture",
                "provisional": False,
                "records": [
                    {
                        "dataset_id": rows.node.node.id,
                        "record_key": row.record.key,
                        "expected_record_revision": row.revision,
                    }
                    for row in rows.records
                ],
            }
        )
        assert len(result.records) == 40
        assert counts["records"] <= 3
        assert counts["resolutions"] <= 3


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_low_level_edges_reject_nonfinite_metadata(tmp_path, value):
    store = Store(tmp_path / "graph.db")
    try:
        edge = Edge(
            project_id="p", type=EdgeType.DEPENDS_ON, src="a", dst="b", data={"weight": value}
        )
        with pytest.raises(ValueError):
            store.upsert_edge(edge)
        assert store.all_edges("p") == []
    finally:
        store.close()
