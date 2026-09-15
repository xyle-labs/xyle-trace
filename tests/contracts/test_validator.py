import pytest

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import (
    Violation,
    blocked_runs,
    is_blocked,
    validate_contracts,
)
from xyle_trace.models.entities import Node, NodeType, Record, Resolution
from xyle_trace.store.sqlite_store import Store

DATASET = "analysis.manifest"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.db")
    s.upsert_node(Node(project_id="p", type=NodeType.DATASET, natural_key=DATASET))
    s.put_records(
        [
            Record(project_id="p", dataset_id=DATASET, key="row0"),
            Record(project_id="p", dataset_id=DATASET, key="row1"),
        ]
    )
    yield s
    s.close()


@pytest.fixture
def contract():
    return Contract(
        dataset=DATASET,
        every_row=["evidence_backed", "assumption_backed"],
        evidence="exact_locator",
        blocks=["analysis.engine.*"],
    )


def _evidence_node(store, locator):
    source = store.upsert_node(Node(project_id="p", type=NodeType.SOURCE, natural_key="source"))
    snapshot = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="snapshot",
            data={"source_id": source.id, "content_hash": "sha256:" + "a" * 64},
        )
    )
    node = Node(
        project_id="p",
        type=NodeType.EVIDENCE_LINK,
        natural_key=f"ev-{locator or 'none'}",
        data={
            "snapshot_id": snapshot.id,
            "locator": {"kind": "table", "value": locator} if locator else None,
        },
    )
    store.upsert_node(node)
    return node


def test_unresolved_records_are_violations(store, contract):
    violations = validate_contracts(store, "p", [contract])
    assert {v.record_key for v in violations} == {"row0", "row1"}
    assert {v.kind for v in violations} == {"unresolved_record"}


def test_fully_resolved_dataset_has_no_violations(store, contract):
    node = _evidence_node(store, "annex II, table 3")
    for key in ("row0", "row1"):
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=DATASET,
                record_key=key,
                status="evidence_backed",
                target_id=node.id,
            )
        )
    assert validate_contracts(store, "p", [contract]) == []


def test_evidence_without_a_locator_is_a_violation(store, contract):
    node = _evidence_node(store, None)
    for key in ("row0", "row1"):
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=DATASET,
                record_key=key,
                status="evidence_backed",
                target_id=node.id,
            )
        )
    violations = validate_contracts(store, "p", [contract])
    assert {v.kind for v in violations} == {"evidence_without_locator"}


def test_assumption_backed_records_do_not_need_a_locator(store, contract):
    decision = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="d1",
        data={"assumption_type": "proxy", "rationale": "Recorded basis for the estimate."},
    )
    store.upsert_node(decision)
    for key in ("row0", "row1"):
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=DATASET,
                record_key=key,
                status="assumption_backed",
                target_id=decision.id,
            )
        )
    assert validate_contracts(store, "p", [contract]) == []


def test_resolution_pointing_at_a_missing_node_is_a_violation(store, contract):
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="evidence_backed",
            target_id="evidence_link:doesnotexist",
        )
    )
    kinds = {v.kind for v in validate_contracts(store, "p", [contract])}
    assert "missing_target" in kinds


def test_resolution_pointing_at_the_wrong_node_type_is_a_violation(store, contract):
    decision = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="d1",
        data={"assumption_type": "proxy", "rationale": "Recorded basis for the estimate."},
    )
    store.upsert_node(decision)
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="evidence_backed",
            target_id=decision.id,
        )
    )
    kinds = {v.kind for v in validate_contracts(store, "p", [contract])}
    assert "target_type_mismatch" in kinds


def test_a_contracted_dataset_with_no_records_is_a_violation(store, contract):
    empty_contract = Contract(
        dataset="does.not.exist", every_row=["evidence_backed"], evidence="exact_locator"
    )
    # The `store` fixture's own dataset has records but is not among the contracts
    # passed here, so it is correctly (and separately) reported as ungoverned;
    # this test only asserts the behaviour for the empty, contracted dataset.
    violations = validate_contracts(store, "p", [empty_contract])
    not_found = [v for v in violations if v.kind == "dataset_not_found"]
    assert len(not_found) == 1
    assert not_found[0].record_key == ""
    assert not_found[0].dataset == "does.not.exist"


def test_a_status_excluded_by_the_contract_is_a_violation(store):
    strict = Contract(dataset=DATASET, every_row=["evidence_backed"])
    decision = Node(
        project_id="p",
        type=NodeType.DECISION,
        natural_key="d1",
        data={"assumption_type": "proxy", "rationale": "Recorded basis for the estimate."},
    )
    store.upsert_node(decision)
    for key in ("row0", "row1"):
        store.put_resolution(
            Resolution(
                project_id="p",
                dataset_id=DATASET,
                record_key=key,
                status="assumption_backed",
                target_id=decision.id,
            )
        )
    assert {v.kind for v in validate_contracts(store, "p", [strict])} == {"unresolved_record"}


def test_violations_block_the_named_runs(contract):
    violations = [
        Violation(dataset=DATASET, kind="unresolved_record", record_key="row0", detail="")
    ]
    assert blocked_runs([contract], violations) == {"analysis.engine.*"}


def test_no_violations_blocks_nothing(contract):
    assert blocked_runs([contract], []) == set()


def test_is_blocked_matches_glob_patterns():
    assert is_blocked("analysis.engine.demand", {"analysis.engine.*"})
    assert not is_blocked("other.run", {"analysis.engine.*"})


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"locator": True}, "invalid_backing"),
        ({"locator": "https://example.test/report"}, "invalid_backing"),
        ({"locator": {"kind": "page", "value": " "}}, "invalid_backing"),
        ({"locator": {"kind": "url", "value": "https://example.test"}}, "invalid_backing"),
        ({"locator": {"kind": "page", "value": 4}}, "invalid_backing"),
        ({"snapshot_id": "missing"}, "invalid_snapshot"),
        ({"provisional": True}, "provisional_backing"),
        ({"provisional": "false"}, "invalid_backing"),
    ],
)
def test_invalid_evidence_cannot_satisfy_contract(store, contract, changes, expected):
    node = _evidence_node(store, "Table 1")
    node.data.update(changes)
    store.upsert_node(node)
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="evidence_backed",
            target_id=node.id,
        )
    )
    violations = validate_contracts(store, "p", [contract])
    assert next(v for v in violations if v.record_key == "row0").kind == expected


@pytest.mark.parametrize(
    "data, expected",
    [
        ({}, "invalid_backing"),
        ({"assumption_type": "proxy"}, "invalid_backing"),
        ({"assumption_type": "proxy", "rationale": " "}, "invalid_backing"),
        (
            {"assumption_type": "proxy", "rationale": "Documented basis", "provisional": True},
            "provisional_backing",
        ),
    ],
)
def test_incomplete_or_provisional_assumptions_do_not_pass(store, contract, data, expected):
    decision = store.upsert_node(
        Node(project_id="p", type=NodeType.DECISION, natural_key="invalid-assumption", data=data)
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="assumption_backed",
            target_id=decision.id,
        )
    )
    violations = validate_contracts(store, "p", [contract])
    assert next(v for v in violations if v.record_key == "row0").kind == expected


@pytest.mark.parametrize(
    "source_project, snapshot_project, digest, expected",
    [
        ("q", "p", "sha256:" + "a" * 64, "invalid_source"),
        ("p", "q", "sha256:" + "a" * 64, "invalid_snapshot"),
        ("p", "p", "a URL is not a hash", "invalid_snapshot"),
    ],
)
def test_snapshot_chain_must_be_hashed_and_project_scoped(
    store, contract, source_project, snapshot_project, digest, expected
):
    source = store.upsert_node(
        Node(project_id=source_project, type=NodeType.SOURCE, natural_key="s")
    )
    snapshot = store.upsert_node(
        Node(
            project_id=snapshot_project,
            type=NodeType.SOURCE_SNAPSHOT,
            natural_key="s1",
            data={"source_id": source.id, "content_hash": digest},
        )
    )
    evidence = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.EVIDENCE_LINK,
            natural_key="e",
            data={"snapshot_id": snapshot.id, "locator": {"kind": "page", "value": "4"}},
        )
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="evidence_backed",
            target_id=evidence.id,
        )
    )
    assert (
        next(v for v in validate_contracts(store, "p", [contract]) if v.record_key == "row0").kind
        == expected
    )


def test_non_assumption_decision_cannot_back_a_record_even_with_assumption_fields(store, contract):
    decision = store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.DECISION,
            natural_key="method",
            data={"kind": "methodology", "assumption_type": "proxy", "rationale": "Some rationale"},
        )
    )
    store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=DATASET,
            record_key="row0",
            status="assumption_backed",
            target_id=decision.id,
        )
    )
    violations = validate_contracts(store, "p", [contract])
    assert next(v for v in violations if v.record_key == "row0").kind == "invalid_backing"
