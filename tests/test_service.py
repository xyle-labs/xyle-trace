from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO

import pytest
from pydantic import TypeAdapter, ValidationError

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import validate_contracts
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.revisions import revision
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models import operations as op
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.queries.traversal import explain
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    contracts = [
        Contract(
            dataset="inputs",
            every_row=["evidence_backed", "assumption_backed"],
            evidence="exact_locator",
        )
    ]
    result = Service(store, "p", contracts=contracts, root=tmp_path)
    yield result
    store.close()


def dataset(service, keys=("a", "b")):
    return service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [{"key": key, "data": {"value": 10}} for key in keys],
        }
    )


def snapshot(service):
    source = service.register(
        {"type": "source", "natural_key": "survey", "data": {"uri": "https://example.test/survey"}}
    )
    (service.store.path.parent / "survey").write_bytes(b"observed values")
    return service.snapshot(
        {"kind": "local", "source_id": source.node.node.id, "path": "survey"}
    ).snapshot.node


def bindings(result):
    return [
        {
            "dataset_id": row.record.dataset_id,
            "record_key": row.record.key,
            "expected_record_revision": row.revision,
            "expected_resolution_revision": row.resolution_revision,
        }
        for row in result.records
    ]


def evidence_request(service, registered):
    return {
        "kind": "evidence",
        "natural_key": "observations",
        "data": {
            "snapshot_id": snapshot(service).id,
            "locator": {"kind": "table", "value": "Table 1"},
            "provisional": False,
        },
        "records": bindings(registered),
    }


def claim(service):
    return service.register(
        {
            "type": "claim",
            "natural_key": "result",
            "data": {"text": "The result is 20", "status": "supported"},
        }
    )


def support(service, dataset_id, claim_id):
    return service.link(
        {
            "kind": "edge",
            "type": "supports",
            "src": dataset_id,
            "dst": claim_id,
            "data": {"provisional": False},
        }
    )


def review(service, result, key="review"):
    return {
        "kind": "review",
        "natural_key": key,
        "rationale": "Checked the observed evidence.",
        "claims": [
            {
                "claim_id": result.node.node.id,
                "expected_revision": result.node.revision,
                "review_status": "reviewed",
            }
        ],
    }


def test_service_resolves_and_explains_a_batch_and_returns_current_revisions(service):
    registered = dataset(service)
    request = evidence_request(service, registered)
    resolved = service.link(request)
    assert resolved.violations == []
    assert validate_contracts(service.store, "p", service.contracts) == []
    assert all(row.resolution.target_id == resolved.node.node.id for row in resolved.records)
    for row in resolved.records:
        assert row.resolution_revision == revision(
            {"status": "evidence_backed", "target_id": resolved.node.node.id}
        )
    assert explain(service.store, "p", registered.node.node.id)["sources"]
    before = export_graph(service.store, "p")
    assert service.link(request) == resolved
    assert export_graph(service.store, "p") == before
    # Both Python models and plain request objects use the same boundary.
    parsed = TypeAdapter(op.LinkRequest).validate_python(request)
    assert service.link(parsed) == resolved


def test_dataset_and_record_revision_checks_are_independent(service):
    first = dataset(service, ("a",))
    write = {
        "type": "dataset",
        "natural_key": "inputs",
        "records": [
            {"key": "a", "data": {"value": 20}, "expected_revision": first.records[0].revision}
        ],
    }
    second = service.register(write)
    assert second.node.revision == first.node.revision
    assert second.records[0].revision != first.records[0].revision
    assert service.register(write) == second
    write["records"][0]["data"] = {"value": 30}
    with pytest.raises(ServiceError) as error:
        service.register(write)
    assert error.value.error.code == "conflict"
    assert error.value.error.details["dataset_id"] == first.node.node.id
    assert error.value.error.details["record_key"] == "a"
    assert service.store.records("p", first.node.node.id)[0].data == {"value": 20}


def test_late_failure_rolls_back_a_new_dataset_and_earlier_records(service):
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError, match="revision conflict"):
        service.register(
            {
                "type": "dataset",
                "natural_key": "inputs",
                "records": [
                    {"key": "a", "data": {"value": 1}},
                    {"key": "b", "data": {"value": 2}, "expected_revision": revision({})},
                ],
            }
        )
    assert export_graph(service.store, "p") == before


def test_stale_resolution_after_record_correction_rolls_back_new_evidence(service):
    first = dataset(service)
    request = evidence_request(service, first)
    service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [
                {"key": "b", "data": {"value": 99}, "expected_revision": first.records[1].revision}
            ],
        }
    )
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.link(request)
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before


def test_resolution_replacement_requires_the_current_resolution_revision(service):
    first = dataset(service, ("a",))
    resolved = service.link(evidence_request(service, first))
    request = {
        "kind": "assumption",
        "natural_key": "proxy",
        "assumption_type": "proxy",
        "rationale": "Use the documented estimate.",
        "provisional": False,
        "records": bindings(first),
    }
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.decide(request)
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before
    request["records"] = bindings(resolved)
    replaced = service.decide(request)
    assert replaced.records[0].resolution.status == "assumption_backed"
    assert replaced.violations == []


def test_backing_update_checks_resolutions_before_invalidating_them(service):
    first = dataset(service)
    request = evidence_request(service, first)
    resolved = service.link(request)
    request["expected_revision"] = resolved.node.revision
    request["data"]["locator"]["value"] = "Table 2"
    request["records"] = bindings(resolved)[:1]
    updated = service.link(request)
    assert updated.records[0].resolution is not None
    assert updated.records[1].resolution is None  # An unmentioned approval is invalidated.
    assert [v.record_key for v in updated.violations] == ["b"]


def test_failed_correction_batch_rolls_back_decision_values_approvals_and_review(service):
    first = dataset(service)
    resolved = service.link(evidence_request(service, first))
    assertion = claim(service)
    support(service, first.node.node.id, assertion.node.node.id)
    service.decide(review(service, assertion))
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError):
        service.decide(
            {
                "kind": "correction",
                "natural_key": "correction",
                "rationale": "Correct inputs.",
                "changes": [
                    {
                        "dataset_id": first.node.node.id,
                        "key": "a",
                        "data": {"value": 40},
                        "expected_revision": resolved.records[0].revision,
                    },
                    {
                        "dataset_id": "missing",
                        "key": "b",
                        "data": {"value": 50},
                        "expected_revision": revision({}),
                    },
                ],
            }
        )
    assert export_graph(service.store, "p") == before


def test_explicit_review_retries_cannot_reapprove_a_later_correction(service):
    first = dataset(service, ("a",))
    assertion = claim(service)
    edge = support(service, first.node.node.id, assertion.node.node.id)
    request = review(service, assertion)
    reviewed = service.decide(request)
    assert reviewed.claims[0].node.data["review_status"] == "reviewed"
    assert reviewed.claims[0].node.data["status"] == "supported"
    assert service.decide(request) == reviewed
    # Repeating an observed edge must not reset review state or timestamps.
    retried_edge = support(service, first.node.node.id, assertion.node.node.id)
    assert retried_edge.edge == edge.edge
    assert retried_edge.claims == []
    service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [
                {"key": "a", "data": {"value": 99}, "expected_revision": first.records[0].revision}
            ],
        }
    )
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.decide(request)
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before
    current = service.store.get_node("p", assertion.node.node.id)
    assert current.data["review_status"] == "needs_review"
    fresh = deepcopy(request)
    fresh["natural_key"] = "new-review"
    fresh["claims"][0]["expected_revision"] = revision(current.data)
    assert service.decide(fresh).claims[0].node.data["review_status"] == "reviewed"


def test_claim_registration_preserves_review_and_content_edits_require_review(service):
    request = {"type": "claim", "natural_key": "result", "data": {"text": "Original claim"}}
    initial = service.register(request)
    reviewed = service.decide(review(service, initial))
    retry = service.register(request)
    assert retry.node == reviewed.claims[0]
    request["data"]["text"] = "Revised claim"
    request["expected_revision"] = retry.node.revision
    changed = service.register(request)
    assert changed.node.node.data["review_status"] == "needs_review"


@pytest.mark.parametrize("provisional", [True, "false"])
def test_provisional_or_mistyped_backing_does_not_resolve_records(service, provisional):
    first = dataset(service)
    request = evidence_request(service, first)
    request["data"]["provisional"] = provisional
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError):
        service.link(request)
    assert export_graph(service.store, "p") == before


def test_provisional_evidence_can_be_retained_without_resolutions(service):
    first = dataset(service)
    request = evidence_request(service, first)
    request["data"]["provisional"] = True
    request["records"] = []
    result = service.link(request)
    assert result.node.node.data["provisional"] is True
    assert service.store.unresolved_keys("p", first.node.node.id) == ["a", "b"]


def test_missing_locator_remains_an_explicit_contract_violation(service):
    first = dataset(service, ("a",))
    request = evidence_request(service, first)
    request["data"].pop("locator")
    result = service.link(request)
    assert result.violations[0].kind == "evidence_without_locator"


@pytest.mark.parametrize(
    "where, value",
    [
        ("provisional", False),
        ("status", "supported"),
        ("review_status", "reviewed"),
        ("project_id", "q"),
        ("content_hash", "sha256:" + "0" * 64),
    ],
)
def test_reserved_metadata_cannot_override_provenance(service, where, value):
    with pytest.raises(ServiceError) as error:
        service.register(
            {
                "type": "source",
                "natural_key": "bad",
                "data": {"uri": "urn:source", "metadata": {where: value}},
            }
        )
    assert error.value.error.code == "invalid_request"
    assert service.store.nodes_of_type("p", NodeType.SOURCE) == []


def test_requests_cannot_override_project_or_register_specialized_nodes(service):
    for request in [
        {"type": "dataset", "natural_key": "bad", "project_id": "q"},
        {"type": "source_snapshot", "natural_key": "bad", "data": {}},
        {"type": "claim", "natural_key": "bad", "data": {"text": "a", "review_status": "reviewed"}},
    ]:
        with pytest.raises(ServiceError) as error:
            service.register(request)
        assert error.value.error.code == "invalid_request"


def test_malformed_or_mutated_typed_input_is_revalidated(service):
    request = op.DatasetRegister(type="dataset", natural_key="inputs")
    request = request.model_copy(
        update={"records": [{"key": "a", "data": {"value": float("nan")}}]}
    )
    with pytest.raises(ServiceError) as error:
        service.register(request)
    assert error.value.error.code == "invalid_request"


def test_duplicate_alias_bindings_and_record_keys_fail_atomically(service):
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError):
        dataset(service, ("a", "a"))
    assert export_graph(service.store, "p") == before
    first = dataset(service, ("a",))
    request = evidence_request(service, first)
    request["records"].append({**request["records"][0], "dataset_id": "inputs"})
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.link(request)
    assert error.value.error.code == "invalid_request"
    assert export_graph(service.store, "p") == before


def test_cross_project_subjects_endpoints_and_snapshots_are_rejected(service):
    other = service.store.upsert_node(
        Node(project_id="q", type=NodeType.SOURCE, natural_key="private")
    )
    local = dataset(service)
    for method, request in [
        (
            service.decide,
            {
                "kind": "methodology",
                "natural_key": "m",
                "rationale": "Basis",
                "subject_ids": [other.id],
            },
        ),
        (
            service.link,
            {
                "kind": "edge",
                "type": "depends_on",
                "src": local.node.node.id,
                "dst": other.id,
                "data": {"provisional": False},
            },
        ),
        (
            service.link,
            {
                "kind": "evidence",
                "natural_key": "e",
                "data": {"snapshot_id": other.id, "provisional": False},
            },
        ),
    ]:
        before = export_graph(service.store, "p")
        with pytest.raises(ServiceError):
            method(request)
        assert export_graph(service.store, "p") == before


def test_edges_validate_endpoint_types_and_keep_contradictions_and_claim_status(service):
    registered = dataset(service)
    assertion = claim(service)
    bad = {
        "kind": "edge",
        "type": "produced",
        "src": registered.node.node.id,
        "dst": assertion.node.node.id,
        "data": {"provisional": False},
    }
    with pytest.raises(ServiceError) as error:
        service.link(bad)
    assert error.value.error.code == "invalid_reference"
    support(service, registered.node.node.id, assertion.node.node.id)
    bad["type"] = "contradicts"
    service.link(bad)
    edges = service.store.edges_from("p", registered.node.node.id)
    assert {e.type.value for e in edges} == {"supports", "contradicts"}
    assert service.store.get_node("p", assertion.node.node.id).data["status"] == "supported"


def test_other_decisions_do_not_fabricate_assumptions_or_remove_records(service):
    registered = dataset(service)
    for kind in ("methodology", "exclusion"):
        result = service.decide(
            {
                "kind": kind,
                "natural_key": kind,
                "rationale": "Documented choice",
                "subject_ids": [registered.node.node.id],
            }
        )
        assert "assumption_type" not in result.node.node.data
    assert len(service.store.records("p", registered.node.node.id)) == 2


@pytest.mark.parametrize(
    "schema",
    [
        op.RegisterRequest,
        op.LinkRequest,
        op.DecideRequest,
        op.SnapshotRequest,
        op.RecordRunRequest,
        op.QueryRequest,
        op.ValidateRequest,
        op.ExportRequest,
    ],
)
def test_eight_operation_schemas_are_publishable(schema):
    import json

    generated = TypeAdapter(schema).json_schema()
    assert isinstance(json.loads(json.dumps(generated)), dict)


def test_future_request_schemas_reject_unsupported_behaviors():
    for schema, data in [
        (op.ExportRequest, {"format": "html"}),
        (op.QueryRequest, {"mode": "execute", "node_id": "n"}),
        (op.QueryRequest, {"mode": "impact", "node_id": "n", "max_depth": 1}),
        (
            op.RecordRunRequest,
            {
                "action": "start",
                "execution_key": "r",
                "transformation_id": "t",
                "input_ids": ["d"],
                "code_reference": None,
                "reproducible": True,
            },
        ),
    ]:
        with pytest.raises(ValidationError):
            TypeAdapter(schema).validate_python(data)


def test_successful_correction_records_a_decision_and_invalidates_prior_backing(service):
    registered = dataset(service, ("a",))
    resolved = service.link(evidence_request(service, registered))
    request = {
        "kind": "correction",
        "natural_key": "fix",
        "rationale": "Correct a transcription.",
        "changes": [
            {
                "dataset_id": "inputs",
                "key": "a",
                "data": {"value": 11},
                "expected_revision": resolved.records[0].revision,
            }
        ],
    }
    result = service.decide(request)
    assert result.node.node.data["kind"] == "correction"
    assert "assumption_type" not in result.node.node.data
    assert result.records[0].resolution is None
    assert result.violations[0].kind == "unresolved_record"
    before = export_graph(service.store, "p")
    assert service.decide(request) == result
    assert export_graph(service.store, "p") == before


def test_late_cross_dataset_failure_restores_an_earlier_applied_correction(service):
    first = dataset(service, ("a",))
    resolved = service.link(evidence_request(service, first))
    other = service.register(
        {
            "type": "dataset",
            "natural_key": "other",
            "records": [{"key": "b", "data": {"value": 50}}],
        }
    )
    assertion = claim(service)
    support(service, first.node.node.id, assertion.node.node.id)
    service.decide(review(service, assertion))
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.decide(
            {
                "kind": "correction",
                "natural_key": "fix",
                "rationale": "Correct both.",
                "changes": [
                    {
                        "dataset_id": first.node.node.id,
                        "key": "a",
                        "data": {"value": 11},
                        "expected_revision": resolved.records[0].revision,
                    },
                    {
                        "dataset_id": other.node.node.id,
                        "key": "b",
                        "data": {"value": 51},
                        "expected_revision": revision({"value": 49}),
                    },
                ],
            }
        )
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before


def test_review_of_multiple_claims_is_atomic_when_last_revision_is_stale(service):
    first = claim(service)
    second = service.register(
        {"type": "claim", "natural_key": "second", "data": {"text": "Second"}}
    )
    request = review(service, first)
    request["claims"].append(
        {
            "claim_id": second.node.node.id,
            "expected_revision": revision({}),
            "review_status": "reviewed",
        }
    )
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.decide(request)
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before


@pytest.mark.parametrize("removed", ["assumption_type", "rationale", "provisional"])
def test_assumptions_require_explicit_basis_and_provisional_state(service, removed):
    request = {
        "kind": "assumption",
        "natural_key": "proxy",
        "assumption_type": "proxy",
        "rationale": "Documented basis",
        "provisional": False,
    }
    del request[removed]
    with pytest.raises(ServiceError) as error:
        service.decide(request)
    assert error.value.error.code == "invalid_request"
    assert service.store.nodes_of_type("p", NodeType.DECISION) == []


def test_service_requires_the_configured_project_to_exist(service):
    with pytest.raises(ServiceError) as error:
        Service(service.store, "not-initialized", contracts=[])
    assert error.value.error.code == "not_found"


def capture_request(service):
    source = service.register(
        {"type": "source", "natural_key": "source", "data": {"uri": "https://example.test/data"}}
    )
    (service.store.path.parent / "input").write_bytes(b"original")
    return {"kind": "local", "source_id": source.node.node.id, "path": "input"}


def test_snapshot_retry_preserves_metadata_and_changed_bytes_make_new_version(service, tmp_path):
    request = capture_request(service)
    first = service.snapshot(request)
    assert not first.already_existed
    assert first.content_hash == hash_bytes(b"original")
    (tmp_path / "input").rename(tmp_path / "moved")
    request["path"] = "moved"
    retry = service.snapshot(request)
    assert retry.already_existed
    assert retry.snapshot == first.snapshot
    assert retry.path == first.path
    (tmp_path / "moved").write_bytes(b"changed")
    changed = service.snapshot(request)
    assert not changed.already_existed
    assert changed.snapshot.node.id != first.snapshot.node.id
    assert (tmp_path / first.path).read_bytes() == b"original"
    assert explain(service.store, "p", changed.snapshot.node.id)["sources"] == [
        request["source_id"]
    ]


def test_identical_bytes_for_different_sources_share_blob_but_not_snapshot(service):
    request = capture_request(service)
    first = service.snapshot(request)
    second_source = service.register(
        {"type": "source", "natural_key": "second", "data": {"uri": "input"}}
    )
    request["source_id"] = second_source.node.node.id
    second = service.snapshot(request)
    assert second.path == first.path
    assert not second.already_existed
    assert second.snapshot.node.id != first.snapshot.node.id


@pytest.mark.parametrize("failure", ["missing", "escape", "corrupt", "http"])
def test_failed_capture_never_writes_graph(service, tmp_path, failure):
    request = capture_request(service)
    if failure == "missing":
        request["path"] = "missing"
    elif failure == "escape":
        request["path"] = "../outside"
    elif failure == "corrupt":
        result = service.snapshot(request)
        (tmp_path / result.path).write_bytes(b"corrupt")
    else:

        @contextmanager
        def timeout(url, seconds):
            raise TimeoutError("do not expose transport secrets")
            yield

        service = Service(service.store, "p", contracts=[], root=tmp_path, http_transport=timeout)
        request = {"kind": "http", "source_id": request["source_id"]}
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.snapshot(request)
    assert error.value.error.code == "capture_failed"
    assert "secrets" not in str(error.value)
    assert export_graph(service.store, "p") == before
    assert list((tmp_path / ".lineage/snapshots").glob(".capture-*")) == []


@pytest.mark.parametrize("reference", ["missing", "dataset", "other_project"])
def test_snapshot_rejects_invalid_source_before_capture(service, tmp_path, reference):
    request = capture_request(service)
    if reference == "dataset":
        request["source_id"] = dataset(service).node.node.id
    elif reference == "other_project":
        foreign = service.store.upsert_node(
            Node(project_id="other", type=NodeType.SOURCE, natural_key="source")
        )
        request["source_id"] = foreign.id
    else:
        request["source_id"] = "missing"
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.snapshot(request)
    assert error.value.error.code == (
        "invalid_reference" if reference == "dataset" else "not_found"
    )
    assert export_graph(service.store, "p") == before
    assert not (tmp_path / ".lineage").exists()


def test_snapshot_requires_trusted_root_and_refuses_outer_transaction(service, tmp_path):
    request = capture_request(service)
    with pytest.raises(ServiceError, match="configured root"):
        Service(service.store, "p", contracts=[]).snapshot(request)
    with service.store.transaction(), pytest.raises(ServiceError, match="write transaction"):
        service.snapshot(request)
    assert not (tmp_path / ".lineage").exists()


def test_snapshot_rechecks_source_after_transfer_without_holding_a_write_lock(
    service, tmp_path, monkeypatch
):
    request = capture_request(service)
    capture = service._capture.local
    source = service.store.get_node("p", request["source_id"])

    def concurrent_edit(path):
        # A second connection can commit while bytes are being captured.
        other = Store(service.store.path)
        try:
            other.upsert_node(source.model_copy(update={"data": {"uri": "https://changed.test"}}))
        finally:
            other.close()
        return capture(path)

    monkeypatch.setattr(service._capture, "local", concurrent_edit)
    with pytest.raises(ServiceError) as error:
        service.snapshot(request)
    assert error.value.error.code == "conflict"
    assert service.store.nodes_of_type("p", NodeType.SOURCE_SNAPSHOT) == []
    assert len(list((tmp_path / ".lineage/snapshots").iterdir())) == 1
    monkeypatch.setattr(service._capture, "local", capture)
    assert not service.snapshot(request).already_existed


def test_failed_graph_commit_keeps_complete_blob_for_retry(service, tmp_path, monkeypatch):
    request = capture_request(service)
    upsert = service.store.upsert_node

    def fail_after_write(node):
        upsert(node)
        raise RuntimeError("simulated commit failure")

    before = export_graph(service.store, "p")
    monkeypatch.setattr(service.store, "upsert_node", fail_after_write)
    with pytest.raises(RuntimeError, match="commit failure"):
        service.snapshot(request)
    assert export_graph(service.store, "p") == before
    paths = list((tmp_path / ".lineage/snapshots").iterdir())
    assert len(paths) == 1 and paths[0].read_bytes() == b"original"
    monkeypatch.setattr(service.store, "upsert_node", upsert)
    assert not service.snapshot(request).already_existed
    assert list((tmp_path / ".lineage/snapshots").iterdir()) == paths


def test_snapshot_request_cannot_override_capture_configuration(service):
    request = capture_request(service)
    request["root"] = "/"
    with pytest.raises(ServiceError) as error:
        service.snapshot(request)
    assert error.value.error.code == "invalid_request"


def test_http_snapshot_uses_registered_url_and_preserves_first_retrieval(service, tmp_path):
    request = capture_request(service)
    calls = []

    class Response(BytesIO):
        status = 200

        def getheader(self, name, default=None):
            return default

    @contextmanager
    def transport(url, timeout):
        assert not service.store.in_transaction
        calls.append(url)
        with Response(b"original") as response:
            yield response

    capture_service = Service(
        service.store, "p", contracts=[], root=tmp_path, http_transport=transport
    )
    first = capture_service.snapshot({"kind": "http", "source_id": request["source_id"]})
    assert calls == ["https://example.test/data"]
    assert first.snapshot.node.data["retrieval"] == {
        "kind": "http",
        "final_url": "https://example.test/data",
    }
    retry = capture_service.snapshot(request)
    assert retry.already_existed
    assert retry.snapshot == first.snapshot
