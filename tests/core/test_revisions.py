import pytest

from xyle_trace.core.revisions import (
    RevisionConflictError,
    canonical_json,
    check_revision,
    require_revision,
    revision,
)


def test_revision_is_stable_across_json_round_trip_and_key_order():
    import json

    first = {"label": "café", "nested": {"b": 2, "a": 1}}
    second = {"nested": {"a": 1, "b": 2}, "label": "café"}
    assert revision(first) == revision(second) == revision(json.loads(canonical_json(first)))
    assert canonical_json(first) == '{"label":"café","nested":{"a":1,"b":2}}'
    assert revision({"value": True}) != revision({"value": 1})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_cannot_have_revisions(value):
    with pytest.raises(ValueError):
        revision({"value": value})


def test_create_retry_update_and_delayed_retry():
    first, second, third = ({"value": value} for value in (1, 2, 3))
    assert check_revision(None, first, None)
    assert not check_revision(first, first, None)
    assert check_revision(first, second, revision(first))
    assert not check_revision(second, second, revision(first))
    with pytest.raises(RevisionConflictError) as error:
        check_revision(third, second, revision(first))
    assert error.value.expected == revision(first)
    assert error.value.actual == revision(third)


def test_creation_does_not_overwrite_existing_data_or_recreate_a_missing_revision():
    with pytest.raises(RevisionConflictError):
        check_revision({"value": 1}, {"value": 2}, None)
    with pytest.raises(RevisionConflictError):
        check_revision(None, {"value": 2}, revision({"value": 1}))


def test_strict_record_revision_does_not_allow_a_stale_resolution_retry():
    require_revision(None, None)
    with pytest.raises(RevisionConflictError):
        require_revision({"value": 2}, revision({"value": 1}))
