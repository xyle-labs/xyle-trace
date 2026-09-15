from xyle_trace.contracts.validator import blocked_runs, validate_contracts


def test_dataset_with_records_and_no_contract_is_reported(project_with_two_datasets):
    """One dataset is contracted, the other is not. The second must not be silent."""
    store, project_id, contracts = project_with_two_datasets
    violations = validate_contracts(store, project_id, contracts)
    ungoverned = [v for v in violations if v.kind == "ungoverned_dataset"]
    assert len(ungoverned) == 1
    assert ungoverned[0].dataset == "scratch_notes"
    assert ungoverned[0].severity == "warn"


def test_ungoverned_dataset_never_blocks_a_run(project_with_two_datasets):
    store, project_id, contracts = project_with_two_datasets
    violations = validate_contracts(store, project_id, contracts)
    assert blocked_runs(contracts, violations) == set()


def test_dataset_with_no_records_is_not_reported(project_with_empty_dataset):
    """An empty dataset is a registration, not ungoverned data."""
    store, project_id, contracts = project_with_empty_dataset
    violations = validate_contracts(store, project_id, contracts)
    assert [v for v in violations if v.kind == "ungoverned_dataset"] == []
