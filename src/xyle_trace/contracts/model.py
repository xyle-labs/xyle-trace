from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]

VALID_STATUSES = {"evidence_backed", "assumption_backed"}
VALID_EVIDENCE = {"exact_locator", "verified_locator"}

# `decision_backed` is the word the spec's example YAML uses; it names the same
# thing as the stored `assumption_backed` status.
STATUS_ALIASES = {"decision_backed": "assumption_backed"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dataset: NonEmptyString
    every_row: list[Literal["evidence_backed", "assumption_backed"]] = Field(min_length=1)
    evidence: Literal["exact_locator", "verified_locator"] | None = None
    blocks: list[NonEmptyString] = Field(default_factory=list)
    severity: Literal["block", "warn"] = "block"
