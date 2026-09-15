from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from xyle_trace.core.ids import make_id


class NodeType(StrEnum):
    PROJECT = "project"
    SOURCE = "source"
    SOURCE_SNAPSHOT = "source_snapshot"
    DATASET = "dataset"
    TRANSFORMATION = "transformation"
    RUN = "run"
    ARTIFACT = "artifact"
    INDICATOR = "indicator"
    CLAIM = "claim"
    EVIDENCE_LINK = "evidence_link"
    AGENT_RUN = "agent_run"
    DECISION = "decision"


class EdgeType(StrEnum):
    SNAPSHOT_OF = "snapshot_of"
    EXTRACTED_FROM = "extracted_from"
    DERIVED_FROM = "derived_from"
    GENERATED_BY = "generated_by"
    USED_INPUT = "used_input"
    PRODUCED = "produced"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    APPEARS_IN = "appears_in"
    SUPERSEDES = "supersedes"
    REVIEWED_BY = "reviewed_by"
    DEPENDS_ON = "depends_on"


# Whether following an edge from src to dst moves toward original evidence
# ("upstream"), away from it ("downstream"), or neither ("lateral").
# Traversal walks upstream edges forward and downstream edges backward.
EDGE_DIRECTION: dict[EdgeType, str] = {
    EdgeType.SNAPSHOT_OF: "upstream",
    EdgeType.EXTRACTED_FROM: "upstream",
    EdgeType.DERIVED_FROM: "upstream",
    EdgeType.GENERATED_BY: "upstream",
    EdgeType.USED_INPUT: "upstream",
    EdgeType.DEPENDS_ON: "upstream",
    EdgeType.PRODUCED: "downstream",
    EdgeType.SUPPORTS: "downstream",
    EdgeType.CONTRADICTS: "downstream",
    EdgeType.APPEARS_IN: "downstream",
    EdgeType.SUPERSEDES: "lateral",
    EdgeType.REVIEWED_BY: "lateral",
}


def _now() -> datetime:
    return datetime.now(UTC)


class Node(BaseModel):
    project_id: str
    type: NodeType
    natural_key: str
    data: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)

    @computed_field
    @property
    def id(self) -> str:
        return make_id(self.project_id, self.type.value, self.natural_key)


class Edge(BaseModel):
    project_id: str
    type: EdgeType
    src: str
    dst: str
    data: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)

    @computed_field
    @property
    def id(self) -> str:
        return make_id(self.project_id, "edge", f"{self.type.value}|{self.src}|{self.dst}")


class Record(BaseModel):
    """One row of a Dataset. Kept out of the node table so that a large
    manifest does not flood the graph."""

    project_id: str
    dataset_id: str
    key: str
    data: dict = Field(default_factory=dict)


class Resolution(BaseModel):
    """How a Record is backed. A record is unresolved exactly when no
    Resolution row exists for it, so 'unresolved' is not a valid status."""

    project_id: str
    dataset_id: str
    record_key: str
    status: Literal["evidence_backed", "assumption_backed"]
    target_id: str
    created_at: datetime = Field(default_factory=_now)
