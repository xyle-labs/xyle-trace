"""Content revisions for optimistic writes; callers own the database transaction."""

import json

from xyle_trace.core.hashing import hash_bytes


class RevisionConflictError(ValueError):
    def __init__(self, expected: str | None, actual: str | None):
        self.expected = expected
        self.actual = actual
        super().__init__(f"revision conflict: expected {expected!r}, found {actual!r}")


def canonical_json(data: dict) -> str:
    """Canonical encoding for persisted JSON data, including Unicode and finite numbers."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def revision(data: dict) -> str:
    return hash_bytes(canonical_json(data).encode("utf-8"))


def require_revision(current: dict | None, expected: str | None) -> None:
    """Require the observed state to still exist. None denotes an absent resource.

    Use this for the record value a resolution backs, even when the resolution
    itself would be an identical retry.
    """
    actual = revision(current) if current is not None else None
    if expected != actual:
        raise RevisionConflictError(expected, actual)


def check_revision(current: dict | None, desired: dict, expected: str | None) -> bool:
    """Return whether a write is needed, accepting exact retries of current data.

    If a later edit changed the value, an old retry must conflict instead of
    restoring the earlier value. Check and write inside Store.transaction().
    """
    desired_revision = revision(desired)
    if current is not None and revision(current) == desired_revision:
        return False
    require_revision(current, expected)
    return True
