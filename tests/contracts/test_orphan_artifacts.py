from xyle_trace.contracts.validator import validate_contracts


def test_artifact_with_no_incoming_edge_is_reported(project_with_orphan_artifact):
    store, project_id, contracts = project_with_orphan_artifact
    violations = validate_contracts(store, project_id, contracts)
    orphans = [v for v in violations if v.kind == "orphan_artifact"]
    assert len(orphans) == 1
    assert orphans[0].dataset == "report.pdf"
    assert orphans[0].severity == "warn"


def test_artifact_produced_by_a_run_is_not_reported(project_with_run_artifact):
    store, project_id, contracts = project_with_run_artifact
    assert [
        v for v in validate_contracts(store, project_id, contracts) if v.kind == "orphan_artifact"
    ] == []


def test_artifact_only_consumed_as_input_is_still_orphan(project_with_used_input_only_artifact):
    """`used_input` proves a run read the file, not what produced it."""
    store, project_id, contracts = project_with_used_input_only_artifact
    orphans = [
        v
        for v in validate_contracts(store, project_id, contracts)
        if v.kind == "orphan_artifact"
    ]
    assert [o.dataset for o in orphans] == ["input.csv"]


def test_artifact_only_appearing_in_a_claim_is_still_orphan(project_with_appears_in_only_artifact):
    """A hand-made file a claim merely cites is the archetype this check exists for."""
    store, project_id, contracts = project_with_appears_in_only_artifact
    orphans = [
        v
        for v in validate_contracts(store, project_id, contracts)
        if v.kind == "orphan_artifact"
    ]
    assert [o.dataset for o in orphans] == ["hand-made-report.pdf"]
