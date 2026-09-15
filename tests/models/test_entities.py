import pytest
from pydantic import ValidationError

from xyle_trace.models.entities import (
    EDGE_DIRECTION,
    Edge,
    EdgeType,
    Node,
    NodeType,
    Record,
    Resolution,
)


def test_there_are_exactly_twelve_node_types():
    assert len(list(NodeType)) == 12


def test_node_id_is_derived_from_natural_key():
    node = Node(project_id="p", type=NodeType.SOURCE, natural_key="psa")
    assert node.id == Node(project_id="p", type=NodeType.SOURCE, natural_key="psa").id
    assert node.id.startswith("source:")


def test_node_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Node(project_id="p", type="spreadsheet", natural_key="x")


def test_edge_id_is_derived_from_endpoints_and_type():
    edge = Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="dataset:a", dst="dataset:b")
    same = Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="dataset:a", dst="dataset:b")
    other = Edge(project_id="p", type=EdgeType.DERIVED_FROM, src="dataset:b", dst="dataset:a")
    assert edge.id == same.id
    assert edge.id != other.id


def test_every_edge_type_has_a_traversal_direction():
    assert set(EDGE_DIRECTION) == set(EdgeType)
    assert set(EDGE_DIRECTION.values()) <= {"upstream", "downstream", "lateral"}


def test_derived_from_points_toward_evidence():
    assert EDGE_DIRECTION[EdgeType.DERIVED_FROM] == "upstream"


def test_produced_points_away_from_evidence():
    assert EDGE_DIRECTION[EdgeType.PRODUCED] == "downstream"


def test_record_carries_arbitrary_payload():
    record = Record(project_id="p", dataset_id="dataset:m", key="row1", data={"unit": "ktoe"})
    assert record.data["unit"] == "ktoe"


def test_resolution_rejects_unresolved_as_a_status():
    with pytest.raises(ValidationError):
        Resolution(
            project_id="p",
            dataset_id="dataset:m",
            record_key="row1",
            status="unresolved",
            target_id="decision:x",
        )
