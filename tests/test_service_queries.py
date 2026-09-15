import json
import os
from pathlib import Path

import pytest

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import blocked_runs, validate_contracts
from xyle_trace.core.hashing import hash_bytes, hash_file
from xyle_trace.core.revisions import canonical_json, revision
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Node, NodeType, Record, Resolution
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    yield Service(store, "p", contracts=[], root=tmp_path)
    store.close()


def dataset(service, key="inputs", keys=("a",)):
    return service.register(
        {
            "type": "dataset",
            "natural_key": key,
            "records": [{"key": k, "data": {"value": 10}} for k in keys],
        }
    )


def configured(service, *contracts):
    return Service(service.store, "p", contracts=list(contracts), root=service.root)


def policy(dataset="inputs", *, blocks=None, statuses=None, evidence=None):
    return Contract(
        dataset=dataset,
        every_row=statuses or ["evidence_backed", "assumption_backed"],
        evidence=evidence,
        blocks=blocks or ["analysis.*"],
    )


def assumption(service, dataset_id, key, *, provisional=False, rationale="Documented proxy"):
    target = service.store.upsert_node(
        Node(
            project_id="p",
            type=NodeType.DECISION,
            natural_key=key,
            data={"assumption_type": "proxy", "rationale": rationale, "provisional": provisional},
        )
    )
    service.store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset_id,
            record_key=key,
            status="assumption_backed",
            target_id=target.id,
        )
    )
    return target


def test_gap_contains_context_for_a_fresh_session_to_resolve_it(service):
    registered = dataset(service)
    contract = policy(evidence="exact_locator")
    service = configured(service, contract)
    gap = service.validate({}).gaps[0]
    assert gap.violation == validate_contracts(service.store, "p", [contract])[0]
    assert gap.dataset_id == registered.node.node.id
    assert gap.record == registered.records[0]
    assert gap.requirements == [contract]
    assert set(gap.permitted_statuses) == {"evidence_backed", "assumption_backed"}
    other = Store(service.store.path)
    try:
        fresh = Service(other, "p", contracts=[contract])
        row = gap.record
        fresh.decide(
            {
                "kind": "assumption",
                "natural_key": "proxy",
                "rationale": "Measured proxy unavailable",
                "assumption_type": "expert_judgement",
                "provisional": False,
                "records": [
                    {
                        "dataset_id": gap.dataset_id,
                        "record_key": row.record.key,
                        "expected_record_revision": row.revision,
                        "expected_resolution_revision": row.resolution_revision,
                    }
                ],
            }
        )
        result = fresh.validate({})
        assert result.valid and result.gaps == []
        assert result.assumptions.assumptions == 1 and result.assumptions.rate == 1
    finally:
        other.close()


def test_scoping_does_not_hide_run_blocking_or_change_global_metrics(service):
    first = dataset(service)
    second = dataset(service, "other")
    assumption(service, second.node.node.id, "a")
    service = configured(service, policy(), policy("other", blocks=["other.*"]))
    for selector in ("other", second.node.node.id):
        result = service.validate({"dataset": selector, "run_name": "analysis.compute"})
        assert result.gaps == [] and not result.valid and result.run_blocked
        assert result.blocked_patterns == ["analysis.*"]
        assert result.assumptions.total == 2 and result.assumptions.rate == 0.5
    assert (
        service.validate({"dataset": first.node.node.id}).gaps[0].dataset_id == first.node.node.id
    )
    assert service.validate({"run_name": "unrelated"}).run_blocked is False


@pytest.mark.parametrize("alias", [True, False])
def test_overlapping_contracts_count_rows_once_and_keep_each_run_requirement(service, alias):
    registered = dataset(service)
    assumption(service, registered.node.node.id, "a")
    service = configured(
        service,
        policy(statuses=["evidence_backed"], blocks=["strict.*"]),
        policy(registered.node.node.id if alias else "inputs", blocks=["lenient.*"]),
    )
    result = service.validate({"run_name": "lenient.compute"})
    assert result.run_blocked is False and result.gaps == []
    assert result.blocked_patterns == ["strict.*"]
    assert blocked_runs(
        service.contracts, validate_contracts(service.store, "p", service.contracts)
    ) == {"strict.*"}
    assert result.assumptions.total == 1 and result.assumptions.invalid == 1
    assert result.assumptions.assumptions == 0
    gap = service.validate({"run_name": "strict.compute"}).gaps[0]
    assert gap.permitted_statuses == ["evidence_backed"]
    assert gap.record.resolution.target_id
    assert gap.record.resolution_revision
    assert len(gap.requirements) == 2


def test_assumption_metrics_separate_missing_invalid_and_provisional_backing(service):
    registered = dataset(service, keys=("valid", "missing", "provisional", "invalid", "unknown"))
    dataset_id = registered.node.node.id
    assumption(service, dataset_id, "valid")
    assumption(service, dataset_id, "provisional", provisional=True)
    assumption(service, dataset_id, "invalid", rationale="")
    service.store.put_resolution(
        Resolution(
            project_id="p",
            dataset_id=dataset_id,
            record_key="unknown",
            status="evidence_backed",
            target_id="missing",
        )
    )
    service = configured(service, policy(), policy(dataset_id))
    metrics = service.validate({}).assumptions
    assert metrics.model_dump() == {
        "total": 5,
        "assumptions": 1,
        "unresolved": 1,
        "invalid": 2,
        "provisional": 1,
        "rate": 0.2,
    }


@pytest.mark.parametrize("registered", [True, False])
def test_missing_and_empty_datasets_have_explicit_gaps_and_no_rate(service, registered):
    if registered:
        dataset(service, keys=())
    result = configured(service, policy()).validate({"dataset": "inputs"})
    assert not result.valid
    assert result.gaps[0].record is None
    assert (result.gaps[0].dataset_id is not None) == registered
    assert result.assumptions.total == 0 and result.assumptions.rate is None


def test_ungoverned_dataset_reports_a_warning_gap_without_invalidating_the_project(service):
    registered = dataset(service, key="scratch_notes")
    result = service.validate({})
    assert result.valid
    assert [gap.violation.kind for gap in result.gaps] == ["ungoverned_dataset"]
    gap = result.gaps[0]
    assert gap.violation.severity == "warn"
    assert gap.violation.dataset == "scratch_notes"
    assert gap.dataset_id == registered.node.node.id
    assert result.blocked_patterns == []
    assert result.run_blocked is None


def test_orphan_artifact_reaches_the_caller_through_service_validate(service):
    """`orphan_artifact` must surface through `Service.validate`, the MCP surface.

    Not only through the lower-level `validate_contracts` helper: the graph-wide
    sweeps in `validate_contracts` are opt-in via `sweeps=`, and `_validation`
    must actually turn them on.
    """
    service.store.upsert_node(
        Node(project_id="p", type=NodeType.ARTIFACT, natural_key="report.pdf")
    )
    result = service.validate({})
    assert result.valid
    assert "orphan_artifact" in [gap.violation.kind for gap in result.gaps]


def test_empty_policy_is_explicit_and_unknown_scope_fails(service):
    result = service.validate({})
    assert result.valid and result.gaps == [] and result.assumptions.rate is None
    with pytest.raises(ServiceError) as error:
        service.validate({"dataset": "typo"})
    assert error.value.error.code == "not_found"


def file_service(service, tmp_path, content=b"[]\n"):
    path = tmp_path / "contracts.yaml"
    path.write_bytes(content)
    return Service(service.store, "p", contracts_path=path, root=tmp_path), path


def test_policy_reload_hashes_and_parses_exactly_one_read(service, tmp_path, monkeypatch):
    dataset(service)
    content = b"- dataset: inputs\r\n  require:\r\n    every_row: [evidence_backed]\r\n"
    service, path = file_service(service, tmp_path, content)
    read = Path.read_bytes
    calls = []

    def read_then_change(current):
        data = read(current)
        if current == path:
            calls.append(current)
            current.write_text("[]")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    result = service.validate({})
    assert len(calls) == 1
    assert result.policy_hash == hash_bytes(content) and not result.valid
    assert service.validate({}).valid


@pytest.mark.parametrize("content", [b"- dataset: [", b"", b"\xff", None])
def test_bad_or_missing_policy_never_falls_back_to_previous_valid_policy(
    service, tmp_path, content
):
    service, path = file_service(service, tmp_path)
    assert service.validate({}).valid
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    with pytest.raises(ServiceError) as error:
        service.validate({})
    assert error.value.error.code == "contract_error"
    # Mutations that report contract violations also fail before any graph writes.
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError):
        dataset(service)
    assert export_graph(service.store, "p") == before
    path.write_bytes(b"[]")
    assert service.validate({}).valid


def test_policy_configuration_is_required_and_unambiguous(service, tmp_path):
    with pytest.raises(ServiceError):
        Service(service.store, "p")
    with pytest.raises(ServiceError):
        Service(service.store, "p", contracts=[], contracts_path=tmp_path / "missing")
    with pytest.raises(ServiceError):
        Service(service.store, "p", contracts_path=tmp_path / "missing")


def chain(service, tmp_path):
    source = service.register({"type": "source", "natural_key": "survey", "data": {"uri": "input"}})
    (tmp_path / "input").write_bytes(b"observations")
    snapshot = service.snapshot(
        {"kind": "local", "source_id": source.node.node.id, "path": "input"}
    )
    rows = dataset(service)
    evidence = service.link(
        {
            "kind": "evidence",
            "natural_key": "table",
            "data": {
                "snapshot_id": snapshot.snapshot.node.id,
                "locator": {"kind": "table", "value": "Table 1"},
                "provisional": False,
            },
            "records": [
                {
                    "dataset_id": rows.node.node.id,
                    "record_key": "a",
                    "expected_record_revision": rows.records[0].revision,
                    "expected_resolution_revision": None,
                }
            ],
        }
    )
    claim = service.register(
        {
            "type": "claim",
            "natural_key": "claim",
            "data": {"text": "Observed result", "status": "unsupported"},
        }
    )
    service.link(
        {
            "kind": "edge",
            "type": "supports",
            "src": rows.node.node.id,
            "dst": claim.node.node.id,
            "data": {"provisional": True},
        }
    )
    return source.node.node.id, rows.node.node.id, evidence.node.node.id, claim.node.node.id


def test_query_explains_qualifiers_records_revisions_and_dependencies(service, tmp_path):
    source, rows, evidence, claim = chain(service, tmp_path)
    result = service.query({"mode": "explain", "node_id": claim})
    assert source in result.source_ids
    assert len(result.records) == 1
    row = result.records[0]
    assert row.record.data == {"value": 10} and row.revision == revision(row.record.data)
    assert row.resolution.target_id == evidence and row.resolution_revision is not None
    assert any(
        pair.consumer == rows and pair.dependency == evidence for pair in result.dependencies
    )
    assert (
        next(node for node in result.nodes if node.node.id == evidence).node.data["locator"][
            "value"
        ]
        == "Table 1"
    )
    assert result.edges[0].edge.data["provisional"] is True
    assert (
        next(node for node in result.nodes if node.node.id == claim).node.data["status"]
        == "unsupported"
    )
    metrics = configured(service, policy(evidence="exact_locator")).validate({}).assumptions
    assert metrics.total == 1 and metrics.rate == 0 and metrics.invalid == 0
    before = export_graph(service.store, "p")
    impact = service.query({"mode": "impact", "node_id": source})
    assert impact.claims_needing_review == [claim]
    assert impact.edges[0].edge.data["provisional"] is True
    assert export_graph(service.store, "p") == before
    assert [
        state.node.id
        for state in service.query({"mode": "upstream", "node_id": claim, "max_depth": 0}).nodes
    ] == [claim]
    assert {
        state.node.id
        for state in service.query({"mode": "upstream", "node_id": claim, "max_depth": 1}).nodes
    } == {claim, rows}
    assert claim in {
        state.node.id for state in service.query({"mode": "downstream", "node_id": source}).nodes
    }


@pytest.mark.parametrize(
    "query_request",
    [
        {"mode": "explain", "node_id": "missing"},
        {"mode": "records", "node_id": "missing"},
        {"mode": "impact", "node_id": "missing", "max_depth": 1},
        {"mode": "upstream", "node_id": "missing", "max_depth": -1},
    ],
)
def test_query_rejects_missing_nodes_and_unsupported_modes_or_depth(service, query_request):
    with pytest.raises(ServiceError):
        service.query(query_request)


def test_query_reads_one_consistent_snapshot_without_blocking_another_writer(service, monkeypatch):
    registered = dataset(service)
    dataset_id = registered.node.node.id
    all_nodes = service.store.all_nodes

    def change_during_read(project_id):
        other = Store(service.store.path)
        try:
            other.put_records(
                [Record(project_id="p", dataset_id=dataset_id, key="a", data={"value": 99})]
            )
        finally:
            other.close()
        return all_nodes(project_id)

    monkeypatch.setattr(service.store, "all_nodes", change_during_read)
    result = service.query({"mode": "explain", "node_id": dataset_id})
    assert result.records[0].record.data == {"value": 10}
    assert service.store.records("p", dataset_id)[0].data == {"value": 99}


def test_inline_and_file_exports_share_hash_and_preserve_legacy_records(service, tmp_path):
    registered = dataset(service)
    # Emulate early databases without passing through the canonical-write API.
    with service.store.transaction():
        service.store._conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?)", ("p", "inputs", "old", '{"value": 1}')
        )
    service = configured(service, policy())
    assert service.validate({}).gaps[0].violation.kind == "legacy_dataset_reference"
    assert service.validate({}).assumptions.invalid == 2
    explained = service.query({"mode": "explain", "node_id": registered.node.node.id})
    assert {row.record.key for row in explained.records} == {"a", "old"}
    inline = service.export({})
    written = service.export({"format": "json", "destination": "exports/graph.json"})
    path = tmp_path / written.path
    assert inline.content_hash == written.content_hash == hash_file(path)
    assert json.loads(path.read_bytes()) == inline.graph == export_graph(service.store, "p")
    assert written.graph is None and inline.path is None
    assert inline.node_count == 2 and inline.edge_count == 0
    assert (
        "inputs" in inline.graph["datasets"] and registered.node.node.id in inline.graph["datasets"]
    )
    assert inline.content_hash == hash_bytes(canonical_json(inline.graph).encode("utf-8"))


def test_html_export_retains_implicit_lineage_and_untrusted_metadata(service, tmp_path):
    import re

    _, rows, evidence, _ = chain(service, tmp_path)
    written = service.export({"format": "html", "destination": "explorer.html"})
    page = (tmp_path / written.path).read_text()
    payload = json.loads(
        re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL)[1]
    )
    assert written.graph is None and written.content_hash == hash_file(tmp_path / written.path)
    assert {"consumer": rows, "dependency": evidence} in payload["dependencies"]
    assert payload["graph"] == service.export({}).graph
    assert written == service.export({"format": "html", "destination": "explorer.html"})


@pytest.mark.parametrize(
    "destination",
    [
        "../escape.json",
        "/tmp/escape.json",
        "graph.db",
        "graph.db-wal",
        "graph.db-shm",
        "graph.db-journal",
        "contracts.yaml",
        ".lineage/snapshots/blob",
        ".lineage/snapshots",
        ".",
    ],
)
def test_export_protects_storage_policy_and_root_without_creating_files(
    service, tmp_path, destination
):
    service, path = file_service(service, tmp_path)
    before = set(tmp_path.rglob("*"))
    with pytest.raises(ServiceError):
        service.export({"destination": destination})
    assert set(tmp_path.rglob("*")) == before
    assert path.read_bytes() == b"[]\n"


@pytest.mark.parametrize("protected", ["database", "policy", "snapshot", "outside"])
@pytest.mark.parametrize("hardlink", [True, False])
def test_export_rejects_aliases_of_protected_files(service, tmp_path, protected, hardlink):
    service, policy_path = file_service(service, tmp_path)
    if protected == "database":
        target = service.store.path
    elif protected == "policy":
        target = policy_path
    elif protected == "snapshot":
        chain(service, tmp_path)
        target = next((tmp_path / ".lineage/snapshots").iterdir())
    else:
        if hardlink:
            return  # Replacing an ordinary hardlink does not modify its outside target.
        target = tmp_path.parent / f"{tmp_path.name}-outside"
        target.write_bytes(b"outside")
    destination = tmp_path / "alias"
    if hardlink:
        os.link(target, destination)
    else:
        destination.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(ServiceError):
        service.export({"destination": "alias"})
    assert target.read_bytes() == before


def test_invalid_format_and_failed_publication_leave_no_partial_export(
    service, tmp_path, monkeypatch
):
    with pytest.raises(ServiceError):
        service.export({"format": "xml", "destination": "new/graph.xml"})
    assert not (tmp_path / "new").exists()
    destination = tmp_path / "graph.json"
    destination.write_bytes(b"previous")

    def fail(*args):
        raise PermissionError("simulated publication failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(ServiceError):
        service.export({"destination": "graph.json"})
    assert destination.read_bytes() == b"previous"
    assert list(tmp_path.glob(".export-*")) == []


def test_query_and_export_are_project_scoped(service):
    other = service.store.upsert_node(
        Node(project_id="other", type=NodeType.DATASET, natural_key="private")
    )
    with pytest.raises(ServiceError):
        service.query({"mode": "explain", "node_id": other.id})
    assert all(node["project_id"] == "p" for node in service.export({}).graph["nodes"])


def test_inline_export_works_without_root_but_file_export_requires_it(service):
    service = Service(service.store, "p", contracts=[])
    assert service.export({}).graph is not None
    with pytest.raises(ServiceError, match="configured root"):
        service.export({"destination": "graph.json"})


def test_validation_does_not_write_review_state_or_resolutions(service, tmp_path):
    chain(service, tmp_path)
    service = configured(service, policy())
    before = export_graph(service.store, "p")
    assert service.validate({}).valid
    assert export_graph(service.store, "p") == before
