import pytest

from xyle_trace.contracts.model import Contract
from xyle_trace.contracts.validator import RunBlockedError, run_guarded
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.revisions import revision
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import EdgeType, Node, NodeType, Record
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    yield Service(store, "p", contracts=[], root=tmp_path)
    store.close()


def start_request(service, key="execution-1"):
    transformation = service.register(
        {"type": "transformation", "natural_key": "analysis.compute", "data": {}}
    )
    dataset = service.register(
        {
            "type": "dataset",
            "natural_key": "inputs",
            "records": [{"key": "a", "data": {"value": 10}}],
        }
    )
    return {
        "action": "start",
        "execution_key": key,
        "transformation_id": transformation.node.node.id,
        "input_ids": [dataset.node.node.id],
        "params": {"factor": 2},
        "code_reference": None,
    }


def output(service, key="result", path=None):
    return service.register(
        {"type": "artifact", "natural_key": key, "data": {"path": path}}
    ).node.node.id


def finish_request(start, outputs, *, status="succeeded", detail=None):
    return {
        "action": "finish",
        "run_id": start.run.node.id,
        "expected_revision": start.run.revision,
        "status": status,
        "outputs": outputs,
        "failure_detail": detail,
    }


def contract():
    return Contract(
        dataset="inputs", every_row=["evidence_backed", "assumption_backed"], blocks=["analysis.*"]
    )


def resolve_input(service, dataset_id):
    row = service.query({"mode": "explain", "node_id": dataset_id}).records[0]
    return service.decide(
        {
            "kind": "assumption",
            "natural_key": "proxy",
            "assumption_type": "proxy",
            "rationale": "Documented estimate",
            "provisional": False,
            "records": [
                {
                    "dataset_id": dataset_id,
                    "record_key": row.record.key,
                    "expected_record_revision": row.revision,
                    "expected_resolution_revision": row.resolution_revision,
                }
            ],
        }
    )


def test_guarded_start_records_receipt_and_hashes_observed_output(service, tmp_path):
    request = start_request(service)
    service = Service(service.store, "p", contracts=[contract()], root=tmp_path)
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "run_blocked"
    assert error.value.error.details["violations"][0]["record_key"] == "a"
    assert export_graph(service.store, "p") == before
    resolve_input(service, request["input_ids"][0])
    started = service.record_run(request)
    assert started.run.node.data["status"] == "started"
    assert started.run.node.data["run_name"] == "analysis.compute"
    assert started.run.node.data["validated_contracts"] == [contract().model_dump(mode="json")]
    assert started.policy_hash == service.validate({}).policy_hash
    assert started.input_file_hashes == {request["input_ids"][0]: None}
    assert set(started.capture_gaps) == {
        "execution_not_observed",
        "environment_unknown",
        "code_reference_unknown",
        "immutable_input_contents_not_retained",
    }
    (tmp_path / "result.txt").write_bytes(b"20")
    result = output(service, path="result.txt")
    finished = service.record_run(finish_request(started, [{"node_id": result}]))
    assert finished.output_file_hashes == {result: hash_bytes(b"20")}
    assert finished.run.node.data["status"] == "succeeded"
    assert finished.run.node.data["finished_at"]
    assert finished.capture_mode == finished.run.node.data["capture_mode"] == "reported"
    assert finished.reproducible is finished.run.node.data["reproducible"] is False
    assert finished.input_fingerprints == started.input_fingerprints
    explanation = service.query({"mode": "explain", "node_id": result})
    assert set(request["input_ids"] + [request["transformation_id"], started.run.node.id]) <= {
        node.node.id for node in explanation.nodes
    }


def test_run_guarded_refuses_the_actual_callable_separately_from_reporting(service):
    request = start_request(service)
    called = []
    with pytest.raises(RunBlockedError):
        run_guarded(
            service.store, "p", "analysis.compute", [contract()], lambda: called.append(True)
        )
    assert called == []
    resolve_input(service, request["input_ids"][0])
    run_guarded(service.store, "p", "analysis.compute", [contract()], lambda: called.append(True))
    assert called == [True]
    captured = service.record_run(request)
    assert called == [True]  # Capture never invokes the computation.
    assert not captured.reproducible and captured.capture_mode == "reported"


def test_start_retries_are_stable_but_new_executions_need_new_keys(service):
    request = start_request(service)
    first = service.record_run(request)
    before = export_graph(service.store, "p")
    assert service.record_run(request) == first
    assert export_graph(service.store, "p") == before
    changed = {**request, "execution_key": "execution-2"}
    second = service.record_run(changed)
    assert second.run.node.id != first.run.node.id
    with pytest.raises(ServiceError) as error:
        service.record_run({**request, "params": {"factor": 3}})
    assert error.value.error.code == "conflict"


def test_terminal_retry_preserves_report_and_delayed_start_does_not_reset_it(service):
    request = start_request(service)
    started = service.record_run(request)
    target = output(service)
    finish = finish_request(started, [{"node_id": target}])
    finished = service.record_run(finish)
    before = export_graph(service.store, "p")
    assert service.record_run(finish) == finished
    assert service.record_run(request) == finished
    assert export_graph(service.store, "p") == before
    assert f"output_file_unavailable:{target}" in finished.capture_gaps
    with pytest.raises(ServiceError) as error:
        service.record_run({**finish, "status": "failed", "failure_detail": "Conflicting report"})
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before


@pytest.mark.parametrize("status", ["failed", "partial"])
def test_failed_and_partial_reports_preserve_details_and_missing_file_gaps(service, status):
    started = service.record_run(start_request(service))
    target = output(service, path="not-produced.txt")
    result = service.record_run(
        finish_request(
            started, [{"node_id": target}], status=status, detail="Output write interrupted"
        )
    )
    assert result.run.node.data["status"] == status
    assert result.run.node.data["failure_detail"] == "Output write interrupted"
    assert result.output_file_hashes == {target: None}
    assert f"output_file_unavailable:{target}" in result.capture_gaps
    assert not result.reproducible
    assert service.store.edges_from("p", started.run.node.id, [EdgeType.PRODUCED]) == []


def test_failure_can_be_reported_without_any_outputs(service):
    started = service.record_run(start_request(service))
    result = service.record_run(
        finish_request(started, [], status="failed", detail="Computation raised an error")
    )
    assert result.output_fingerprints == {}
    assert result.run.node.data["status"] == "failed"


def test_finish_rejects_stale_revision_before_touching_files(service):
    started = service.record_run(start_request(service))
    target = output(service, path="missing.txt")
    request = finish_request(started, [{"node_id": target}])
    request["expected_revision"] = revision({})
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "conflict"
    assert export_graph(service.store, "p") == before


@pytest.mark.parametrize("missing", ["node", "file"])
def test_missing_success_output_does_not_commit_success(service, missing):
    started = service.record_run(start_request(service))
    target = "unknown" if missing == "node" else output(service, path="missing.txt")
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.record_run(finish_request(started, [{"node_id": target}]))
    assert error.value.error.code == ("not_found" if missing == "node" else "capture_failed")
    assert export_graph(service.store, "p") == before


def test_start_checks_new_policy_even_for_an_existing_execution(service, tmp_path):
    request = start_request(service)
    path = tmp_path / "contracts.yaml"
    path.write_bytes(b"[]\n")
    service = Service(service.store, "p", contracts_path=path, root=tmp_path)
    started = service.record_run(request)
    assert started.policy_hash == hash_bytes(b"[]\n")
    path.write_text(
        "- dataset: inputs\n  require:\n    every_row: [evidence_backed]\n  blocks: [analysis.*]\n"
    )
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "run_blocked"
    path.write_text("malformed: [")
    with pytest.raises(ServiceError) as error:
        service.record_run({**request, "execution_key": "new"})
    assert error.value.error.code == "contract_error"
    assert service.store.nodes_of_type("p", NodeType.RUN) == [started.run.node]
    # Finishing reports an outcome; a later policy error must not erase it.
    failure = service.record_run(
        finish_request(started, [], status="failed", detail="Stopped after policy changed")
    )
    assert failure.run.node.data["status"] == "failed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_name", "unblocked"),
        ("reproducible", True),
        ("capture_mode", "enforced"),
        ("input_fingerprints", {}),
    ],
)
def test_start_cannot_override_guard_or_claim_reproducibility(service, field, value):
    request = start_request(service)
    with pytest.raises(ServiceError) as error:
        service.record_run({**request, field: value})
    assert error.value.error.code == "invalid_request"
    assert service.store.nodes_of_type("p", NodeType.RUN) == []


@pytest.mark.parametrize(
    "invalid",
    ["no_success_outputs", "no_failure_detail", "success_with_failure", "claimed_reproducibility"],
)
def test_finish_requires_an_explicit_consistent_report(service, invalid):
    started = service.record_run(start_request(service))
    request = finish_request(started, [{"node_id": output(service)}])
    if invalid == "no_success_outputs":
        request["outputs"] = []
    elif invalid == "no_failure_detail":
        request["status"] = "partial"
    elif invalid == "success_with_failure":
        request["failure_detail"] = "Actually failed"
    else:
        request["reproducible"] = True
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "invalid_request"
    assert service.store.get_node("p", started.run.node.id) == started.run.node


@pytest.mark.parametrize("stage", ["input", "output", "transformation"])
@pytest.mark.parametrize("foreign", [True, False])
def test_run_references_require_correct_types_in_this_project(service, stage, foreign):
    request = start_request(service)
    node = service.store.upsert_node(
        Node(project_id="other" if foreign else "p", type=NodeType.CLAIM, natural_key="wrong")
    )
    if stage == "output":
        started = service.record_run(request)
        request = finish_request(started, [{"node_id": node.id}])
    elif stage == "input":
        request["input_ids"] = [node.id]
    else:
        request["transformation_id"] = node.id
    before = export_graph(service.store, "p")
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == ("not_found" if foreign else "invalid_reference")
    assert export_graph(service.store, "p") == before


def test_fingerprints_cover_row_values_and_current_resolutions(service):
    request = start_request(service)
    first = service.record_run(request)
    target = request["input_ids"][0]
    resolve_input(service, target)
    resolved = service.record_run({**request, "execution_key": "resolved"})
    assert resolved.input_fingerprints[target] != first.input_fingerprints[target]
    service.store.put_records(
        [Record(project_id="p", dataset_id=target, key="a", data={"value": 11})]
    )
    changed = service.record_run({**request, "execution_key": "changed"})
    assert changed.input_fingerprints[target] != resolved.input_fingerprints[target]
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "conflict"


def test_input_file_hashes_include_verified_snapshots(service, tmp_path):
    request = start_request(service)
    source = service.register(
        {"type": "source", "natural_key": "source", "data": {"uri": "input.txt"}}
    )
    (tmp_path / "input.txt").write_bytes(b"source data")
    snapshot = service.snapshot(
        {"kind": "local", "source_id": source.node.node.id, "path": "input.txt"}
    )
    request["input_ids"] = [snapshot.snapshot.node.id]
    request["code_reference"] = "script.py@abc123"
    result = service.record_run(request)
    assert result.input_file_hashes == {snapshot.snapshot.node.id: hash_bytes(b"source data")}
    assert "immutable_input_contents_not_retained" not in result.capture_gaps
    assert "code_reference_unknown" not in result.capture_gaps
    assert not result.reproducible
    (tmp_path / snapshot.path).write_bytes(b"corrupt")
    with pytest.raises(ServiceError) as error:
        service.record_run({**request, "execution_key": "corrupt"})
    assert error.value.error.code == "capture_failed"


@pytest.mark.parametrize("path", ["../outside", "/etc/hosts", "symlink"])
def test_run_file_paths_cannot_escape_root(service, tmp_path, path):
    request = start_request(service)
    (tmp_path / "symlink").symlink_to(tmp_path.parent)
    target = output(service, path=path)
    request["input_ids"] = [target]
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "invalid_reference"
    started = service.record_run(
        {**request, "input_ids": [service.store.dataset_node("p", "inputs").id]}
    )
    with pytest.raises(ServiceError):
        service.record_run(
            finish_request(started, [{"node_id": target}], status="failed", detail="Failed")
        )


def test_restart_preserves_unfinished_run_without_executing_or_completing_it(service):
    request = start_request(service)
    started = service.record_run(request)
    other = Store(service.store.path)
    try:
        fresh = Service(other, "p", contracts=[])
        assert fresh.record_run(request) == started
        assert fresh.query({"mode": "explain", "node_id": started.run.node.id}).nodes
        assert other.get_node("p", started.run.node.id).data["status"] == "started"
    finally:
        other.close()


def test_state_changes_during_file_hashing_conflict_without_holding_write_lock(
    service, tmp_path, monkeypatch
):
    request = start_request(service)
    (tmp_path / "input.txt").write_bytes(b"input")
    artifact = output(service, path="input.txt")
    request["input_ids"].append(artifact)
    from xyle_trace import service as module

    original = module.hash_file

    def concurrent_edit(path):
        assert not service.store.in_transaction
        other = Store(service.store.path)
        try:
            other.put_records(
                [
                    Record(
                        project_id="p",
                        dataset_id=request["input_ids"][0],
                        key="a",
                        data={"value": 99},
                    )
                ]
            )
        finally:
            other.close()
        return original(path)

    monkeypatch.setattr(module, "hash_file", concurrent_edit)
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "conflict"
    assert service.store.nodes_of_type("p", NodeType.RUN) == []


def test_run_node_and_edges_roll_back_together(service, monkeypatch):
    request = start_request(service)
    write = service.store.upsert_edge

    def fail_on_input(edge):
        write(edge)
        if edge.type == EdgeType.USED_INPUT:
            raise RuntimeError("simulated failure")

    before = export_graph(service.store, "p")
    monkeypatch.setattr(service.store, "upsert_edge", fail_on_input)
    with pytest.raises(RuntimeError):
        service.record_run(request)
    assert export_graph(service.store, "p") == before


def test_duplicates_and_mismatched_output_path_are_rejected(service):
    request = start_request(service)
    with pytest.raises(ServiceError, match="duplicate"):
        service.record_run({**request, "input_ids": request["input_ids"] * 2})
    started = service.record_run(request)
    target = output(service, path="registered.txt")
    with pytest.raises(ServiceError, match="duplicate"):
        service.record_run(finish_request(started, [{"node_id": target}] * 2))
    with pytest.raises(ServiceError, match="disagrees"):
        service.record_run(finish_request(started, [{"node_id": target, "path": "other.txt"}]))


def test_changed_output_cannot_rewrite_a_terminal_receipt(service, tmp_path):
    started = service.record_run(start_request(service))
    (tmp_path / "output.txt").write_bytes(b"first")
    target = output(service, path="output.txt")
    request = finish_request(started, [{"node_id": target}])
    finished = service.record_run(request)
    (tmp_path / "output.txt").write_bytes(b"later")
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "conflict"
    assert service.store.get_node("p", finished.run.node.id) == finished.run.node


def test_legacy_run_without_receipt_cannot_be_finished(service):
    run = service.store.upsert_node(Node(project_id="p", type=NodeType.RUN, natural_key="legacy"))
    with pytest.raises(ServiceError) as error:
        service.record_run(
            {
                "action": "finish",
                "run_id": run.id,
                "expected_revision": revision({}),
                "status": "failed",
                "failure_detail": "Unknown start",
            }
        )
    assert error.value.error.code == "invalid_reference"


def test_finish_and_downstream_review_roll_back_if_an_edge_write_fails(service, monkeypatch):
    started = service.record_run(start_request(service))
    target = output(service)
    claim = service.register({"type": "claim", "natural_key": "claim", "data": {"text": "Result"}})
    service.link(
        {
            "kind": "edge",
            "type": "supports",
            "src": target,
            "dst": claim.node.node.id,
            "data": {"provisional": False},
        }
    )
    before = export_graph(service.store, "p")
    write = service.store.upsert_edge

    def fail(edge):
        write(edge)
        raise RuntimeError("simulated edge failure")

    monkeypatch.setattr(service.store, "upsert_edge", fail)
    with pytest.raises(RuntimeError):
        service.record_run(finish_request(started, [{"node_id": target}]))
    assert export_graph(service.store, "p") == before


def test_competing_finish_reports_cannot_replace_a_committed_outcome(service):
    started = service.record_run(start_request(service))
    other = Store(service.store.path)
    try:
        competing = Service(other, "p", contracts=[])
        request = finish_request(started, [], status="failed", detail="Observed error")
        first = service.record_run(request)
        assert competing.record_run(request) == first
        with pytest.raises(ServiceError) as error:
            competing.record_run({**request, "failure_detail": "Different observation"})
        assert error.value.error.code == "conflict"
    finally:
        other.close()


def test_output_edit_during_hashing_rejects_finish(service, tmp_path, monkeypatch):
    from xyle_trace import service as module

    started = service.record_run(start_request(service))
    (tmp_path / "result.txt").write_bytes(b"result")
    target = output(service, path="result.txt")
    original = module.hash_file

    def edit_node(path):
        assert not service.store.in_transaction
        other = Store(service.store.path)
        try:
            node = other.get_node("p", target)
            other.upsert_node(
                node.model_copy(update={"data": {**node.data, "description": "changed"}})
            )
        finally:
            other.close()
        return original(path)

    monkeypatch.setattr(module, "hash_file", edit_node)
    with pytest.raises(ServiceError) as error:
        service.record_run(finish_request(started, [{"node_id": target}]))
    assert error.value.error.code == "conflict"
    assert service.store.get_node("p", started.run.node.id).data["status"] == "started"


def test_file_changed_while_hashing_is_not_recorded(service, tmp_path, monkeypatch):
    from xyle_trace import service as module

    request = start_request(service)
    (tmp_path / "input.txt").write_bytes(b"first")
    request["input_ids"] = [output(service, path="input.txt")]
    original = module.hash_file

    def edit_file(path):
        result = original(path)
        path.write_bytes(b"different content")
        return result

    monkeypatch.setattr(module, "hash_file", edit_file)
    with pytest.raises(ServiceError) as error:
        service.record_run(request)
    assert error.value.error.code == "conflict"
    assert service.store.nodes_of_type("p", NodeType.RUN) == []


def test_run_start_hashes_exactly_one_fresh_policy_read(service, tmp_path, monkeypatch):
    from pathlib import Path

    request = start_request(service)
    path = tmp_path / "contracts.yaml"
    path.write_bytes(b"[]\n")
    service = Service(service.store, "p", contracts_path=path, root=tmp_path)
    read = Path.read_bytes
    calls = []

    def read_then_change(current):
        data = read(current)
        if current == path:
            calls.append(current)
            current.write_text("invalid: [")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    result = service.record_run(request)
    assert calls == [path] and result.policy_hash == hash_bytes(b"[]\n")
    with pytest.raises(ServiceError) as error:
        service.record_run({**request, "execution_key": "next"})
    assert error.value.error.code == "contract_error"


def test_dataset_and_indicator_outputs_are_recorded_without_inventing_file_hashes(service):
    started = service.record_run(start_request(service))
    dataset = service.register(
        {
            "type": "dataset",
            "natural_key": "derived",
            "records": [{"key": "answer", "data": {"value": 20}}],
        }
    )
    indicator = service.register(
        {"type": "indicator", "natural_key": "total", "data": {"definition": "Total"}}
    )
    ids = [dataset.node.node.id, indicator.node.node.id]
    result = service.record_run(finish_request(started, [{"node_id": node_id} for node_id in ids]))
    assert set(result.output_fingerprints) == set(ids)
    assert result.output_file_hashes == dict.fromkeys(ids)
    assert {
        edge.dst for edge in service.store.edges_from("p", started.run.node.id, [EdgeType.PRODUCED])
    } == set(ids)


def test_run_capture_refuses_an_outer_write_transaction(service):
    request = start_request(service)
    with service.store.transaction(), pytest.raises(ServiceError, match="write transaction"):
        service.record_run(request)
    assert service.store.nodes_of_type("p", NodeType.RUN) == []
