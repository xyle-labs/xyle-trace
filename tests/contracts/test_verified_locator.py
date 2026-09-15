from xyle_trace.contracts.loader import parse_contracts
from xyle_trace.contracts.validator import validate_contracts


def test_verified_locator_requires_an_anchor(project_with_evidence):
    """A prose locator with no anchor satisfies exact_locator but not verified_locator."""
    store, project_id, _ = project_with_evidence
    contracts = parse_contracts(
        """
        - dataset: observations
          require:
            every_row: [evidence_backed]
            evidence: verified_locator
        """
    )
    violations = validate_contracts(store, project_id, contracts)
    assert [v.kind for v in violations] == ["evidence_without_anchor"]


def test_anchor_that_is_absent_from_the_snapshot_is_a_violation(project_with_bad_anchor):
    store, project_id, contracts, snapshot_root = project_with_bad_anchor
    violations = validate_contracts(store, project_id, contracts, snapshot_root=snapshot_root)
    assert [v.kind for v in violations] == ["locator_not_found"]


def test_anchor_that_resolves_passes(project_with_good_anchor):
    store, project_id, contracts, snapshot_root = project_with_good_anchor
    assert validate_contracts(store, project_id, contracts, snapshot_root=snapshot_root) == []


def test_corrupted_snapshot_bytes_are_a_distinct_violation(project_with_corrupted_snapshot):
    """Re-hashing on read: a matching filename is not proof of matching bytes."""
    store, project_id, contracts, snapshot_root = project_with_corrupted_snapshot
    violations = validate_contracts(store, project_id, contracts, snapshot_root=snapshot_root)
    assert [v.kind for v in violations] == ["locator_snapshot_mismatch"]


def test_existing_exact_locator_contracts_are_unaffected(project_with_evidence):
    """Backwards compatibility: prose locators keep passing exact_locator."""
    store, project_id, _ = project_with_evidence
    contracts = parse_contracts(
        """
        - dataset: observations
          require:
            every_row: [evidence_backed]
            evidence: exact_locator
        """
    )
    assert validate_contracts(store, project_id, contracts) == []


def test_symlinked_snapshot_cannot_satisfy_a_contract(project_with_good_anchor, tmp_path):
    store, project_id, contracts, snapshot_root = project_with_good_anchor
    blob = next(snapshot_root.iterdir())
    outside = tmp_path / "outside-snapshot"
    blob.rename(outside)
    blob.symlink_to(outside)
    issues = validate_contracts(store, project_id, contracts, snapshot_root=snapshot_root)
    assert [issue.kind for issue in issues] == ["locator_unverifiable"]
