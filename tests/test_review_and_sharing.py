import json
import zipfile

import pytest

from xyle_trace.core.hashing import hash_file
from xyle_trace.exporters.json_graph import export_graph
from xyle_trace.models.entities import Node, NodeType
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import Store


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path / ".lineage/graph.db")
    store.upsert_node(Node(project_id="p", type=NodeType.PROJECT, natural_key="p"))
    policy = tmp_path / "contracts.yaml"
    policy.write_text("[]")
    service = Service(store, "p", contracts_path=policy, root=tmp_path)
    output = tmp_path / "result.json"
    output.write_text('{"total":10}')
    rows = service.register({"type": "dataset", "natural_key": "inputs"})
    source = service.register(
        {
            "type": "source",
            "natural_key": "source",
            "data": {"uri": "https://example.org/input?token=secret-url"},
        }
    )
    transform = service.register({"type": "transformation", "natural_key": "calculate", "data": {}})
    artifact = service.register(
        {
            "type": "artifact",
            "natural_key": "Coverage",
            "data": {"path": "result.json", "metadata": {"value": 10, "unit": "%"}},
        }
    )
    run = service.record_run(
        {
            "action": "start",
            "execution_key": "run-1",
            "transformation_id": transform.node.node.id,
            "input_ids": [rows.node.node.id],
            "code_reference": "calculate.py",
        }
    )
    service.record_run(
        {
            "action": "finish",
            "run_id": run.run.node.id,
            "expected_revision": run.run.revision,
            "status": "succeeded",
            "outputs": [{"node_id": artifact.node.node.id}],
        }
    )
    service.link(
        {
            "kind": "edge",
            "type": "depends_on",
            "src": artifact.node.node.id,
            "dst": source.node.node.id,
            "data": {"provisional": False},
        }
    )
    service.register(
        {
            "type": "claim",
            "natural_key": "Unrelated private conclusion",
            "data": {"text": "unrelated-private-data"},
        }
    )
    yield service, artifact.node.node.id
    store.close()


def test_health_separates_policy_success_from_output_drift_and_pending_review(project):
    service, artifact = project
    (service.root / "result.json").write_text('{"total":20}')
    before = export_graph(service.store, "p")
    result = service.validate({"health": True})
    assert result.valid
    assert result.health["policy"]["valid"]
    assert result.health["review"]["pending_claim_ids"]
    observed = next(x for x in result.health["integrity"]["artifacts"] if x["node_id"] == artifact)
    assert observed["status"] == "changed"
    assert export_graph(service.store, "p") == before


def test_selected_report_preview_and_written_content_match_without_unrelated_data(project):
    service, artifact = project
    preview = service.export({"format": "report", "selected_ids": [artifact]})
    assert preview.path is None
    assert "unrelated-private-data" not in json.dumps(preview.graph)
    assert "secret-url" not in json.dumps(preview.graph)
    assert preview.graph["explanations"][artifact]["selection"]["node_id"] == artifact
    result = service.export(
        {"format": "report", "selected_ids": [artifact], "destination": "reviewed-report.html"}
    )
    page = (service.root / result.path).read_text()
    assert result.content_hash == preview.content_hash == hash_file(service.root / result.path)
    assert "unrelated-private-data" not in page and "secret-url" not in page
    assert "Coverage" in page and "Reported" in page


def test_reports_require_real_results_and_protect_policy(project):
    service, artifact = project
    for request in (
        {"format": "report"},
        {"format": "report", "selected_ids": ["missing"]},
        {"format": "report", "selected_ids": [artifact], "destination": "contracts.yaml"},
    ):
        with pytest.raises(ServiceError):
            service.export(request)
    assert (service.root / "contracts.yaml").read_text() == "[]"


def test_bundle_export_is_explicit_and_records_content_omissions(project):
    service, _ = project
    result = service.export({"format": "bundle", "destination": "project.zip"})
    with zipfile.ZipFile(service.root / result.path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["include_content"] is False
        assert "contracts.yaml" in archive.namelist()
    assert result.content_hash == hash_file(service.root / result.path)
