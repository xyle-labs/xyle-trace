"""Adversarial result-selection checks; reachability alone is not production proof."""

import json

import pytest

from xyle_trace.core.execution import execute
from xyle_trace.core.hashing import hash_bytes
from xyle_trace.core.revisions import canonical_json
from xyle_trace.core.snapshots import SnapshotCapture
from xyle_trace.models.entities import EdgeType, Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def project(tmp_path):
    with_store = Store(tmp_path / ".lineage/graph.db")
    with_store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    service = Service(with_store, "p", contracts=[], root=tmp_path)
    yield service
    with_store.close()


def managed(project, *, key="first", inputs=None, value=7):
    dataset = project.register(
        {
            "type": "dataset",
            "natural_key": f"inputs-{key}",
            "records": [{"key": "a", "data": {"value": value}}],
        }
    )
    transform = project.register(
        {
            "type": "transformation",
            "natural_key": f"compute-{key}",
            "data": {},
        }
    )
    script = f"{key}.py"
    (project.root / script).write_text(
        "import json,sys\n"
        "d=json.load(open(sys.argv[1]))\n"
        "v=sum(r['record']['data']['value'] for x in d['inputs'].values() "
        "for r in x.get('records', []))\n"
        "json.dump({'total':v,'a/b':{'~key':[None,False,0]}},open(sys.argv[2],'w'))\n"
    )
    run = execute(
        project,
        key=key,
        transformation_id=transform.node.node.id,
        input_ids=inputs or [dataset.node.node.id],
        script=script,
        params={},
    )
    assert run["data"]["status"] == "succeeded"
    artifact = next(
        node
        for node in project.store.nodes_of_type("p", NodeType.ARTIFACT)
        if node.data.get("path") == run["data"]["output"]["path"] and run["id"] in node.natural_key
    )
    return dataset, run, artifact


def reported(project, value=23):
    rows = project.register({"type": "dataset", "natural_key": "reported-inputs"})
    transform = project.register(
        {
            "type": "transformation",
            "natural_key": "external",
            "data": {},
        }
    )
    output = project.root / "reported.json"
    output.write_text(json.dumps({"total": value}))
    artifact = project.register(
        {
            "type": "artifact",
            "natural_key": "reported-result",
            "data": {"path": output.name},
        }
    ).node.node
    started = project.record_run(
        {
            "action": "start",
            "execution_key": "external",
            "transformation_id": transform.node.node.id,
            "input_ids": [rows.node.node.id],
            "code_reference": None,
        }
    )
    run = project.record_run(
        {
            "action": "finish",
            "run_id": started.run.node.id,
            "expected_revision": started.run.revision,
            "status": "succeeded",
            "outputs": [{"node_id": artifact.id}],
        }
    ).run.node
    return run, artifact


def explanation(project, artifact, **selection):
    return project.query({"mode": "explain", "node_id": artifact.id, **selection}).explanation


def replace_bundle(project, run, value):
    payload = canonical_json(value).encode("utf-8")
    blob = SnapshotCapture(project.root).bytes(payload)
    node = project.store.get_node("p", run["id"])
    node.data["bundle"] = {"path": blob.path, "content_hash": blob.content_hash}
    project.store.upsert_node(node)


def test_explicit_upstream_run_cannot_replace_the_actual_producer(project):
    _, original, first_output = managed(project)
    _, later, second_output = managed(project, key="second", inputs=[first_output.id])
    assert later["id"] != original["id"]
    # The earlier run is reachable through the later run's input. It did not
    # produce the later output, and selecting it must not return its total.
    with pytest.raises(ServiceError) as error:
        explanation(project, second_output, run_id=original["id"], selector="/total")
    assert error.value.error.code == "invalid_reference"


def test_claim_reachable_through_contradiction_is_not_a_run_output(project):
    _, run, artifact = managed(project)
    claim = project.register(
        {
            "type": "claim",
            "natural_key": "contradicted-claim",
            "data": {"text": "The total was 100", "status": "unsupported"},
        }
    ).node.node
    project.link(
        {
            "kind": "edge",
            "type": "contradicts",
            "src": artifact.id,
            "dst": claim.id,
            "data": {"provisional": False},
        }
    )
    with pytest.raises(ServiceError) as error:
        explanation(project, claim, run_id=run["id"], selector="/total")
    assert error.value.error.code == "invalid_reference"


@pytest.mark.parametrize("explicit", [False, True])
def test_provisional_production_is_not_promoted_to_observed_output(project, explicit):
    _, run, artifact = managed(project)
    produced = project.store.edges_to("p", artifact.id, [EdgeType.PRODUCED])[0]
    produced.data["provisional"] = True
    project.store.upsert_edge(produced)
    try:
        result = explanation(
            project, artifact, selector="/total", **({"run_id": run["id"]} if explicit else {})
        )
    except ServiceError as error:
        assert error.error.code == "invalid_reference"
    else:
        assert result["selection"]["value"] is None
        assert any(gap["kind"] == "provisional_relationship" for gap in result["gaps"])


def test_failed_managed_run_does_not_claim_successful_execution(project):
    _, run, _ = managed(project)
    node = project.store.get_node("p", run["id"])
    node.data.update(status="failed", failure_detail="Historical replay output mismatch")
    project.store.upsert_node(node)
    result = explanation(project, node)
    assert result["execution"]["status"] == "failed"
    assert result["execution"]["reproducible"] is False
    assert result["execution"]["state"] not in {"executed", "replayed"}


def test_interrupted_managed_run_is_started_rather_than_executed(project):
    _, run, _ = managed(project)
    node = project.store.get_node("p", run["id"])
    node.data.update(status="started")
    node.data.pop("output")
    project.store.upsert_node(node)
    result = explanation(project, node)
    assert result["execution"]["state"] == "started"
    assert result["execution"]["reproducible"] is False
    assert result["selection"]["value_available"] is False


@pytest.mark.parametrize(
    "pointer,expected",
    [
        ("/a~1b/~0key/0", None),
        ("/a~1b/~0key/1", False),
        ("/a~1b/~0key/2", 0),
    ],
)
def test_pointer_escapes_and_false_values_remain_distinct(project, pointer, expected):
    _, _, artifact = managed(project)
    result = explanation(project, artifact, selector=pointer)
    assert result["selection"]["value"] is expected
    assert result["selection"]["value_available"] is True


def test_unavailable_output_is_distinct_from_a_recorded_json_null(project):
    _, run, artifact = managed(project)
    (project.root / run["data"]["output"]["path"]).unlink()
    result = explanation(project, artifact, selector="/a~1b/~0key/0")
    assert result["selection"]["value"] is None
    assert result["selection"]["value_available"] is False
    assert any(gap["kind"] == "output_unavailable" for gap in result["gaps"])


def test_hash_checked_reported_null_is_available(project):
    _, artifact = reported(project, value=None)
    selection = explanation(project, artifact, selector="/total")["selection"]
    assert selection["value"] is None
    assert selection["value_available"] is True


@pytest.mark.parametrize("metadata", [False, True])
def test_explicit_metadata_null_has_a_value_but_absent_metadata_does_not(project, metadata):
    data = {"metadata": {"value": None}} if metadata else {"value": None}
    recorded = Node(
        project_id="p", type=NodeType.INDICATOR, natural_key="recorded-null",
        data={"definition": "Synthetic null result", **data},
    )
    project.store.upsert_node(recorded)
    missing = project.register(
        {"type": "indicator", "natural_key": "missing-value", "data": {"definition": "Missing"}}
    ).node.node
    assert explanation(project, recorded)["selection"]["value_available"] is True
    assert explanation(project, missing)["selection"]["value_available"] is False


@pytest.mark.parametrize("pointer", ["/absent", "/a~1b/~0key/9", "/a~1b/~0key/-1"])
def test_missing_selected_field_is_a_typed_not_found(project, pointer):
    _, _, artifact = managed(project)
    with pytest.raises(ServiceError) as error:
        explanation(project, artifact, selector=pointer)
    assert error.value.error.code == "not_found"


@pytest.mark.parametrize("mutation", ["parent", "absolute", "symlink"])
def test_reported_output_paths_cannot_escape_root(project, mutation):
    _, artifact = reported(project)
    external = project.root.parent / "outside.json"
    external.write_text(json.dumps({"total": 23}))
    assert hash_bytes(external.read_bytes()) == hash_bytes(
        (project.root / "reported.json").read_bytes()
    )
    if mutation == "parent":
        artifact.data["path"] = "../outside.json"
    elif mutation == "absolute":
        artifact.data["path"] = str(external)
    else:
        (project.root / "escape.json").symlink_to(external)
        artifact.data["path"] = "escape.json"
    project.store.upsert_node(artifact)
    result = explanation(project, artifact, selector="/total")
    assert result["selection"]["value"] is None
    assert any(gap["kind"] == "output_unavailable" for gap in result["gaps"])


@pytest.mark.parametrize(
    "bundle",
    [
        {"version": 1, "input": {"inputs": {"missing-node": {}}}},
        {"version": 1, "input": {"inputs": {}}},
        {"version": 1, "input": {"inputs": {"broken": {"node": [], "records": []}}}},
        [],
    ],
)
def test_schema_corrupt_retained_bundle_is_a_gap_not_an_untyped_crash(project, bundle):
    _, run, artifact = managed(project)
    replace_bundle(project, run, bundle)
    result = explanation(project, artifact, selector="/total")
    assert result["selection"]["historical"] is False
    assert result["selection"]["value"] is None
    assert any(gap["kind"] == "retained_inputs_unavailable" for gap in result["gaps"])


@pytest.mark.parametrize("replay", [False, True])
def test_valid_hash_bundle_from_a_different_run_cannot_explain_selected_output(project, replay):
    _, first, artifact = managed(project)
    if replay:
        first = execute(project, key="replay", replay_of=first["id"])
        artifact = next(
            node for node in project.store.nodes_of_type("p", NodeType.ARTIFACT)
            if first["id"] in node.natural_key
        )
    _, other, _ = managed(project, key="other", value=99)
    selected_run = project.store.get_node("p", first["id"])
    selected_run.data["bundle"] = other["data"]["bundle"]
    project.store.upsert_node(selected_run)
    result = explanation(project, artifact, selector="/total")
    assert result["selection"]["historical"] is False
    assert result["selection"]["value_available"] is False
    assert any(gap["kind"] == "retained_inputs_unavailable" for gap in result["gaps"])
    assert not result["inputs"] or all(i["basis"] == "current_context" for i in result["inputs"])


def test_managed_run_cannot_substitute_its_output_for_another_linked_artifact(project):
    _, run, _ = managed(project)
    other = project.register(
        {"type": "artifact", "natural_key": "other-output", "data": {"path": "different.json"}}
    ).node.node
    project.link({"kind": "edge", "type": "produced", "src": run["id"], "dst": other.id,
                  "data": {"provisional": False}})
    result = explanation(project, other, selector="/total")
    assert result["selection"]["value_available"] is False
    assert result["selection"]["value"] is None
    assert any(gap["kind"] == "output_reference_mismatch" for gap in result["gaps"])


@pytest.mark.parametrize("field", ["params", "policy_hash", "environment", "project_id"])
def test_retained_bundle_context_must_agree_with_run_receipt(project, field):
    _, run, artifact = managed(project)
    bundle = json.loads((project.root / run["data"]["bundle"]["path"]).read_bytes())
    if field == "params":
        bundle["input"]["params"] = {"factor": 123}
    elif field == "project_id":
        next(iter(bundle["input"]["inputs"].values()))["node"]["project_id"] = "different"
    else:
        bundle[field] = "different"
    replace_bundle(project, run, bundle)
    result = explanation(project, artifact)
    assert result["selection"]["historical"] is False
    assert result["selection"]["value_available"] is False
    assert any(gap["kind"] == "retained_inputs_unavailable" for gap in result["gaps"])


def test_replay_bundle_must_be_original_even_when_relationships_match(project):
    _, original, _ = managed(project)
    replay = execute(project, key="replay", replay_of=original["id"])
    artifact = next(node for node in project.store.nodes_of_type("p", NodeType.ARTIFACT)
                    if replay["id"] in node.natural_key)
    bundle = json.loads((project.root / replay["data"]["bundle"]["path"]).read_bytes())
    bundle["input"]["params"] = {"changed": True}
    replace_bundle(project, replay, bundle)
    result = explanation(project, artifact)
    assert result["selection"]["historical"] is False
    assert result["selection"]["value_available"] is False


def test_corrupt_retained_bytes_never_substitute_current_result_metadata(project):
    dataset, run, artifact = managed(project)
    project.decide(
        {
            "kind": "correction",
            "natural_key": "new-observation",
            "rationale": "New evidence pending",
            "changes": [
                {
                    "dataset_id": dataset.node.node.id,
                    "key": "a",
                    "data": {"value": 999},
                    "expected_revision": dataset.records[0].revision,
                }
            ],
        }
    )
    artifact.data["metadata"] = {"value": 999}
    project.store.upsert_node(artifact)
    (project.root / run["data"]["bundle"]["path"]).write_bytes(b"corrupted")
    result = explanation(project, artifact)
    assert result["selection"]["historical"] is False
    assert result["selection"]["value"] is None
    assert all(not item["records"] for item in result["inputs"])
    assert any(gap["kind"] == "retained_inputs_unavailable" for gap in result["gaps"])


def test_intact_retained_bundle_survives_current_input_and_code_changes(project):
    dataset, run, artifact = managed(project)
    project.decide(
        {
            "kind": "correction",
            "natural_key": "new-observation",
            "rationale": "New evidence pending",
            "changes": [
                {
                    "dataset_id": dataset.node.node.id,
                    "key": "a",
                    "data": {"value": 999},
                    "expected_revision": dataset.records[0].revision,
                }
            ],
        }
    )
    (project.root / "first.py").write_text("raise RuntimeError('current code must not run')")
    result = explanation(project, artifact, run_id=run["id"], selector="/total", include_code=True)
    assert result["selection"]["value"] == 7
    assert result["inputs"][0]["records"][0]["record"]["data"] == {"value": 7}
    assert "json.dump" in result["calculations"][0]["code"]
    assert all(item["basis"] == "retained" for item in result["inputs"])


def test_unsafe_retained_output_reference_does_not_inspect_an_external_file(project):
    _, run, artifact = managed(project)
    node = project.store.get_node("p", run["id"])
    node.data["output"]["path"] = "../external-output.json"
    (project.root.parent / "external-output.json").write_text('{"total":777}')
    project.store.upsert_node(node)
    result = explanation(project, artifact, selector="/total")
    assert result["selection"]["value"] is None
    assert any(gap["kind"] == "output_unavailable" for gap in result["gaps"])


def test_reused_reported_artifact_cannot_serve_new_bytes_for_an_older_run(project):
    first, artifact = reported(project)
    transform = project.register(
        {
            "type": "transformation",
            "natural_key": "new-external",
            "data": {},
        }
    )
    rows = project.register({"type": "dataset", "natural_key": "new-inputs"})
    (project.root / "reported.json").write_text('{"total":999}')
    second = project.record_run(
        {
            "action": "start",
            "execution_key": "new-external",
            "transformation_id": transform.node.node.id,
            "input_ids": [rows.node.node.id],
            "code_reference": None,
        }
    )
    project.record_run(
        {
            "action": "finish",
            "run_id": second.run.node.id,
            "expected_revision": second.run.revision,
            "status": "succeeded",
            "outputs": [{"node_id": artifact.id}],
        }
    )
    ambiguous = explanation(project, artifact, selector="/total")
    assert ambiguous["selection"]["run_id"] is None
    assert ambiguous["selection"]["value"] is None
    assert any(gap["kind"] == "ambiguous_producer" for gap in ambiguous["gaps"])
    earlier = explanation(project, artifact, run_id=first.id, selector="/total")
    assert earlier["selection"]["value"] is None
    assert any(gap["kind"] == "output_hash_mismatch" for gap in earlier["gaps"])
    latest = explanation(project, artifact, run_id=second.run.node.id, selector="/total")
    assert latest["selection"]["value"] == 999
    assert latest["execution"]["state"] == "reported"
    assert latest["execution"]["reproducible"] is False


def test_positive_support_does_not_replace_claim_text_with_run_output_metadata(project):
    _, _, artifact = managed(project)
    claim = project.register(
        {
            "type": "claim",
            "natural_key": "supported-claim",
            "data": {"text": "The synthetic total was 7", "status": "analytical_inference"},
        }
    ).node.node
    project.link(
        {
            "kind": "edge",
            "type": "supports",
            "src": artifact.id,
            "dst": claim.id,
            "data": {"provisional": False},
        }
    )
    response = project.query({"mode": "explain", "node_id": claim.id})
    selected = next(state.node for state in response.nodes if state.node.id == claim.id)
    assert selected.data["text"] == claim.data["text"]
    assert selected.data["status"] == claim.data["status"]
    assert response.explanation["selection"]["value"] is None


def test_managed_explanation_preserves_the_selected_nodes_actual_revision(project):
    _, _, artifact = managed(project)
    response = project.query({"mode": "explain", "node_id": artifact.id})
    selected = next(state for state in response.nodes if state.node.id == artifact.id)
    assert selected.node.data == artifact.data
    assert selected.revision == response.explanation["selection"]["revision"]
    pinned = project.query(
        {
            "mode": "explain",
            "node_id": artifact.id,
            "revision": selected.revision,
        }
    )
    assert pinned.explanation["selection"]["node_id"] == artifact.id


def test_selecting_a_run_cannot_be_redirected_to_its_replay_ancestor(project):
    _, original, _ = managed(project)
    replay = execute(project, key="replay", replay_of=original["id"])
    selected_run = project.store.get_node("p", replay["id"])
    with pytest.raises(ServiceError) as error:
        explanation(project, selected_run, run_id=original["id"], selector="/total")
    assert error.value.error.code == "invalid_reference"
