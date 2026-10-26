import re

import pytest

from xyle_trace.core.revisions import revision
from xyle_trace.exporters.report import render_report


@pytest.fixture
def graph():
    return {
        "project_id": "p",
        "nodes": [
            {"id": "result", "type": "indicator", "natural_key": "Coverage", "data": {
                "value": 42, "unit": "%", "scope": "2026", "revision": 3,
                "review_status": "needs_review"}},
            {"id": "run", "type": "run", "natural_key": "Compute", "data": {
                "status": "succeeded", "capture_mode": "reported", "reproducible": False}},
            {"id": "source", "type": "source_snapshot", "natural_key": "Capture", "data": {
                "content_hash": "sha256:abc", "text": "DO NOT SHARE SOURCE BYTES"}},
            {"id": "other", "type": "claim", "natural_key": "Unselected claim", "data": {}},
        ],
        "edges": [
            {"src": "result", "dst": "run", "type": "generated_by", "data": {}},
            {"src": "run", "dst": "source", "type": "used_input", "data": {"provisional": True}},
            {"src": "other", "dst": "source", "type": "depends_on", "data": {}},
        ],
        "datasets": {},
    }


def test_report_requires_explicit_selection_and_limits_to_upstream_closure(graph):
    with pytest.raises(ValueError, match="selected"):
        render_report(graph, [], selected_ids=[])
    report = render_report(graph, [], selected_ids=["result"],
                           explanations={"result": {"gaps": ["Input record unresolved"]}})
    assert "Coverage" in report and "Compute" in report and "Capture" in report
    assert "Unselected claim" not in report
    assert "Input record unresolved" in report
    assert "Provisional" in report
    assert "Reported" in report
    assert "sha256:abc" in report
    assert "DO NOT SHARE SOURCE BYTES" not in report
    assert 'href="#result-' in report
    changed = {**graph, "nodes": [{**graph["nodes"][0], "data": {
        **graph["nodes"][0]["data"], "value": 43}}, *graph["nodes"][1:]]}
    assert re.search(r'href="#(result-[0-9a-f]+)', report)[1] != re.search(
        r'href="#(result-[0-9a-f]+)', render_report(changed, [], selected_ids=["result"]))[1]
    assert "@media print" in report
    assert '<script src=' not in report and '<link ' not in report


@pytest.mark.parametrize("tag", ["script", "SCRIPT", "ScRiPt"])
def test_report_escapes_input_and_states_missing_explanation(graph, tag):
    graph["nodes"][0]["natural_key"] = '<img src=x onerror="alert(1)">'
    report = render_report(graph, [], selected_ids=["result"], title=f"<{tag}>boom</{tag}>")
    assert "<img" not in report and f"<{tag}>boom" not in report
    assert "&lt;img" in report and f"&lt;{tag}&gt;boom" in report
    assert "Explanation not available" in report
    assert "Evidence not recorded" in report
    assert not re.search(r"<script(?:\s|>)", report, re.IGNORECASE)


def test_report_uses_typed_metadata_and_selected_value_without_uri_secrets(graph):
    graph["nodes"][0]["data"] = {"metadata": {"value": 40, "unit": "%", "scope": "older scope"},
                                   "review_status": "reviewed"}
    graph["nodes"][2]["data"] = {
        "content_hash": "sha256:abc",
        "uri": "https://user:pass@example.test/source?token=private#fragment",
        "retrieval_url": "https://example.test/retrieve?api_key=hidden#key",
        "text": "source body must stay out",
    }
    graph["nodes"][3]["data"] = {"metadata": {"value": "unselected private result"}}
    report = render_report(
        graph, [], selected_ids=["result"],
        explanations={"result": {"selection": {"value": 42},
                                 "evidence": [{"retrieval_url": "https://viewer.test/item?token=also-private#part"}]}},
    )
    assert "42 %" in report and "40 %" not in report
    assert "older scope" in report and "Reviewed" in report
    assert "https://example.test/source" in report
    assert "https://example.test/retrieve" in report
    assert "https://viewer.test/item" in report
    for secret in ("user:pass", "private", "fragment", "api_key=", "hidden", "also-private",
                   "unselected private result", "source body must stay out"):
        assert secret not in report


def test_report_keeps_shared_input_versions_with_their_selected_result():
    def node(node_id, kind, description):
        return {"id": node_id, "type": kind, "natural_key": node_id,
                "data": {"description": description}}

    first = node("first", "artifact", "First output")
    second = node("second", "artifact", "Second output")
    shared_current = node("shared", "dataset", "Current global input")
    shared_first = node("shared", "dataset", "Retained input version A")
    shared_second = node("shared", "dataset", "Retained input version B")
    graph = {"project_id": "p", "nodes": [first, second, shared_current],
             "edges": [], "datasets": {},
             "contexts": {
                 "first": {"nodes": [first, shared_first], "edges": [
                     {"src": "first", "dst": "shared", "type": "depends_on", "data": {}}],
                     "dependencies": [], "datasets": {}},
                 "second": {"nodes": [second, shared_second], "edges": [
                     {"src": "second", "dst": "shared", "type": "depends_on", "data": {}}],
                     "dependencies": [], "datasets": {}},
             }}
    report = render_report(graph, [], selected_ids=["first", "second"])
    articles = re.findall(r"<article\b.*?</article>", report, re.DOTALL)
    assert len(articles) == 2
    assert "Retained input version A" in articles[0]
    assert "Retained input version B" not in articles[0]
    assert "Retained input version B" in articles[1]
    assert "Retained input version A" not in articles[1]
    assert "Current global input" not in report


def test_report_prints_supplied_original_revision_after_uri_projection(graph):
    source = graph["nodes"][2]
    source["data"]["uri"] = "https://reader:password@example.test/source?token=private#part"
    original_revision = revision(source["data"])
    source["revision"] = original_revision
    report = render_report(graph, [], selected_ids=["result"])
    assert f"Version {original_revision}" in report
    assert "https://example.test/source" in report
    assert "reader:password" not in report and "token=private" not in report


@pytest.mark.parametrize("value,display", [(None, "null"), ({}, "{}"), ([], "[]"),
                                          (False, "false"), (0, "0"), ("", '&quot;&quot;')])
def test_report_preserves_recorded_json_values_instead_of_marking_them_missing(graph, value, display):
    report = render_report(
        graph, [], selected_ids=["result"],
        explanations={"result": {"selection": {"value": value, "value_available": True}}},
    )
    assert f'<p class="value">{display} %</p>' in report


def test_report_marks_unavailable_value_without_using_metadata_fallback(graph):
    report = render_report(
        graph, [], selected_ids=["result"],
        explanations={"result": {"selection": {"value": None, "value_available": False}}},
    )
    assert '<p class="value">Not recorded %</p>' in report
    assert '<p class="value">42 %</p>' not in report
    assert '<dt>Value</dt><dd>null</dd>' not in report
