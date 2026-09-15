from xyle_trace.contracts.loader import parse_contracts
from xyle_trace.contracts.validator import blocked_runs, validate_contracts


def test_warn_contract_reports_but_does_not_block(project_with_unresolved_record):
    store, project_id, _ = project_with_unresolved_record
    contracts = parse_contracts(
        """
        - dataset: observations
          severity: warn
          require:
            every_row: [evidence_backed]
          blocks: [analysis.*]
        """
    )
    violations = validate_contracts(store, project_id, contracts)
    assert [v.kind for v in violations] == ["unresolved_record"]
    assert all(v.severity == "warn" for v in violations)
    assert blocked_runs(contracts, violations) == set()


def test_block_is_the_default_severity(project_with_unresolved_record):
    store, project_id, contracts = project_with_unresolved_record  # no severity key
    violations = validate_contracts(store, project_id, contracts)
    assert all(v.severity == "block" for v in violations)
    assert blocked_runs(contracts, violations) == {"analysis.*"}
