"""Transport-independent schemas for the eight implemented service operations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import Violation
from xyle_trace.models.entities import Edge, EdgeType, Node, Record, Resolution
from xyle_trace.models.provenance import ContentHash, Locator, Text


class OperationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, revalidate_instances="always"
    )


class Metadata(OperationModel):
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_reserved_metadata(self):
        reserved = {
            "id",
            "project_id",
            "type",
            "kind",
            "natural_key",
            "created_at",
            "revision",
            "expected_revision",
            "expected_record_revision",
            "status",
            "review_status",
            "review_decision_id",
            "reviewed_claim_revisions",
            "provisional",
            "snapshot_id",
            "source_id",
            "content_hash",
            "assumption_type",
            "rationale",
            "reproducible",
            "capture_mode",
            "dataset_id",
            "record_key",
            "target_id",
        }
        if reserved.intersection(self.metadata):
            raise ValueError("metadata cannot contain reserved provenance or revision fields")
        return self


class SourceData(Metadata):
    uri: Text
    title: Text | None = None


class DatasetData(Metadata):
    description: Text | None = None


class ArtifactData(Metadata):
    path: Text | None = None
    description: Text | None = None


ClaimStatus = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "conflicting_sources",
    "analytical_inference",
    "editorial_interpretation",
]


class ClaimData(Metadata):
    text: Text
    status: ClaimStatus = "unsupported"


class IndicatorData(Metadata):
    definition: Text
    unit: Text | None = None
    formula: Text | None = None


class TransformationData(Metadata):
    description: Text | None = None
    code_reference: Text | None = None


class AgentRunData(Metadata):
    actor: Text
    task: Text | None = None


class NodeWrite(OperationModel):
    natural_key: Text
    expected_revision: ContentHash | None = None


class RecordWrite(OperationModel):
    key: Text
    data: dict[str, JsonValue]
    expected_revision: ContentHash | None = None


class SourceRegister(NodeWrite):
    type: Literal["source"]
    data: SourceData


class DatasetRegister(NodeWrite):
    type: Literal["dataset"]
    data: DatasetData = Field(default_factory=DatasetData)
    records: list[RecordWrite] = Field(default_factory=list)


class ArtifactRegister(NodeWrite):
    type: Literal["artifact"]
    data: ArtifactData


class ClaimRegister(NodeWrite):
    type: Literal["claim"]
    data: ClaimData


class IndicatorRegister(NodeWrite):
    type: Literal["indicator"]
    data: IndicatorData


class TransformationRegister(NodeWrite):
    type: Literal["transformation"]
    data: TransformationData


class AgentRunRegister(NodeWrite):
    type: Literal["agent_run"]
    data: AgentRunData


RegisterRequest = Annotated[
    SourceRegister
    | DatasetRegister
    | ArtifactRegister
    | ClaimRegister
    | IndicatorRegister
    | TransformationRegister
    | AgentRunRegister,
    Field(discriminator="type"),
]


class RecordBinding(OperationModel):
    dataset_id: Text
    record_key: Text
    expected_record_revision: ContentHash
    expected_resolution_revision: ContentHash | None = None


class EvidenceInput(Metadata):
    snapshot_id: Text
    locator: Locator | None = None
    provisional: bool  # Deliberately required at the agent-facing boundary.


class EvidenceLinkRequest(NodeWrite):
    kind: Literal["evidence"]
    data: EvidenceInput
    records: list[RecordBinding] = Field(default_factory=list)


class EdgeQualifiers(Metadata):
    provisional: bool
    locator: Locator | None = None
    rationale: Text | None = None


class EdgeLinkRequest(OperationModel):
    kind: Literal["edge"]
    type: EdgeType = Field(strict=False)
    src: Text
    dst: Text
    data: EdgeQualifiers
    expected_revision: ContentHash | None = None


LinkRequest = Annotated[EdgeLinkRequest | EvidenceLinkRequest, Field(discriminator="kind")]


class DecisionWrite(NodeWrite, Metadata):
    rationale: Text


class AssumptionRequest(DecisionWrite):
    kind: Literal["assumption"]
    assumption_type: Text
    provisional: bool
    records: list[RecordBinding] = Field(default_factory=list)


class RecordCorrection(RecordWrite):
    dataset_id: Text
    expected_revision: ContentHash  # Corrections require an observed existing value.


class CorrectionRequest(DecisionWrite):
    kind: Literal["correction"]
    changes: list[RecordCorrection] = Field(min_length=1)


class SubjectDecisionRequest(DecisionWrite):
    kind: Literal["exclusion", "methodology"]
    subject_ids: list[Text] = Field(min_length=1)


class ClaimReview(OperationModel):
    claim_id: Text
    expected_revision: ContentHash
    review_status: Literal["reviewed", "needs_review"]


class ReviewRequest(DecisionWrite):
    kind: Literal["review"]
    claims: list[ClaimReview] = Field(min_length=1)


DecideRequest = Annotated[
    AssumptionRequest | CorrectionRequest | SubjectDecisionRequest | ReviewRequest,
    Field(discriminator="kind"),
]


class NodeState(OperationModel):
    node: Node
    revision: ContentHash


class EdgeState(OperationModel):
    edge: Edge
    revision: ContentHash


class RecordState(OperationModel):
    record: Record
    revision: ContentHash
    resolution: Resolution | None = None
    resolution_revision: ContentHash | None = None


class MutationResult(OperationModel):
    node: NodeState
    records: list[RecordState] = Field(default_factory=list)
    claims: list[NodeState] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)


class EdgeResult(OperationModel):
    edge: EdgeState
    claims: list[NodeState] = Field(default_factory=list)


class ErrorDetail(OperationModel):
    code: Literal[
        "invalid_request",
        "not_found",
        "invalid_reference",
        "conflict",
        "contract_error",
        "run_blocked",
        "capture_failed",
        "unsupported_operation",
    ]
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


# Schemas for Tasks 3–5. These are contracts, not placeholder service handlers.
class LocalSnapshotRequest(OperationModel):
    kind: Literal["local"]
    source_id: Text
    path: Text


class RemoteSnapshotRequest(OperationModel):
    kind: Literal["http"]
    source_id: Text  # URL comes from the registered source.


SnapshotRequest = Annotated[
    LocalSnapshotRequest | RemoteSnapshotRequest, Field(discriminator="kind")
]


class SnapshotResult(OperationModel):
    snapshot: NodeState
    content_hash: ContentHash
    size_bytes: int = Field(ge=0)
    path: Text
    already_existed: bool


class StartRunRequest(OperationModel):
    """Record preflight for external execution; does not execute or isolate inputs."""

    action: Literal["start"]
    execution_key: Text
    transformation_id: Text
    input_ids: list[Text] = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    code_reference: Text | None  # Null explicitly represents unknown code.


class RunOutput(OperationModel):
    node_id: Text
    path: Text | None = None


class FinishRunRequest(OperationModel):
    """Report an observed outcome, never certify server-executed computation."""

    action: Literal["finish"]
    run_id: Text
    expected_revision: ContentHash
    status: Literal["succeeded", "failed", "partial"]
    outputs: list[RunOutput] = Field(default_factory=list)
    failure_detail: Text | None = None

    @model_validator(mode="after")
    def outcome_is_explicit(self):
        if self.status == "succeeded":
            if not self.outputs or self.failure_detail is not None:
                raise ValueError("success requires outputs and no failure detail")
        elif self.failure_detail is None:
            raise ValueError("failed or partial runs require a failure detail")
        return self


RecordRunRequest = Annotated[StartRunRequest | FinishRunRequest, Field(discriminator="action")]


class RunResult(OperationModel):
    run: NodeState
    input_fingerprints: dict[str, ContentHash]
    output_fingerprints: dict[str, ContentHash]
    input_file_hashes: dict[str, ContentHash | None] = Field(default_factory=dict)
    output_file_hashes: dict[str, ContentHash | None] = Field(default_factory=dict)
    capture_gaps: list[Text] = Field(default_factory=list)
    policy_hash: ContentHash
    violations: list[Violation] = Field(default_factory=list)
    capture_mode: Literal["reported"] = "reported"
    reproducible: Literal[False] = False


class WalkRequest(OperationModel):
    mode: Literal["upstream", "downstream"]
    node_id: Text
    max_depth: int | None = Field(default=None, ge=0)


class ExplainRequest(OperationModel):
    mode: Literal["explain"]
    node_id: Text


class ImpactRequest(OperationModel):
    mode: Literal["impact"]
    node_id: Text


class ContentRequest(OperationModel):
    mode: Literal["content"]
    node_id: Text


QueryRequest = Annotated[
    WalkRequest | ExplainRequest | ImpactRequest | ContentRequest, Field(discriminator="mode")
]


class Dependency(OperationModel):
    consumer: Text
    dependency: Text


class ContentRef(OperationModel):
    path: Text
    content_hash: ContentHash
    size_bytes: int = Field(ge=0)
    verified: bool
    # Not `Text`: that alias strips whitespace and forbids the empty string, so
    # a blank or whitespace-only captured file would either fail validation or
    # come back silently altered from the bytes `verified` was computed against.
    # This must be the decoded bytes byte-for-byte, or not present at all.
    text: str | None = None


class QueryResult(OperationModel):
    mode: Literal["upstream", "downstream", "explain", "impact", "content"]
    node_id: Text
    nodes: list[NodeState]
    edges: list[EdgeState] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    records: list[RecordState] = Field(default_factory=list)
    source_ids: list[Text] = Field(default_factory=list)
    claims_needing_review: list[Text] = Field(default_factory=list)
    content: ContentRef | None = None


class ValidateRequest(OperationModel):
    dataset: Text | None = None
    run_name: Text | None = None


class Gap(OperationModel):
    violation: Violation
    dataset_id: Text | None
    record: RecordState | None
    permitted_statuses: list[Literal["evidence_backed", "assumption_backed"]]
    requirements: list[Contract] = Field(default_factory=list)


class AssumptionMetrics(OperationModel):
    total: int = Field(ge=0)
    assumptions: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    invalid: int = Field(ge=0)
    provisional: int = Field(ge=0)
    rate: float | None = Field(ge=0, le=1)


class ValidateResult(OperationModel):
    valid: bool
    gaps: list[Gap]
    blocked_patterns: list[Text]
    run_blocked: bool | None
    policy_hash: ContentHash
    assumptions: AssumptionMetrics


class ExportRequest(OperationModel):
    format: Literal["json", "html"] = "json"
    destination: Text | None = None

    @model_validator(mode="after")
    def html_requires_destination(self):
        if self.format == "html" and self.destination is None:
            raise ValueError("HTML export requires a destination")
        return self


class ExportResult(OperationModel):
    graph: dict[str, JsonValue] | None
    path: Text | None
    content_hash: ContentHash
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
