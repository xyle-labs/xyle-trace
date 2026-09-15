"""Metadata required to back a record; extra descriptive metadata is retained."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class Locator(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["page", "table", "section", "path", "range", "fragment"]
    value: Text
    # A literal string that must occur in the snapshot's decoded text. Optional,
    # because a locator into a binary PDF has nothing to match against; a
    # contract asking for `verified_locator` is what makes it required.
    anchor: Text | None = None


class SnapshotData(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    source_id: Text
    content_hash: ContentHash


class EvidenceData(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    snapshot_id: Text
    locator: Locator | None = None
    provisional: bool = False


class DecisionData(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    assumption_type: Text
    rationale: Text
    provisional: bool = False
