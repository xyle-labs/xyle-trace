import json
import re
import shutil
import subprocess

import pytest

from xyle_trace.exporters.html_graph import render_html


def _payload(page):
    return json.loads(
        re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL)[1]
    )


def test_untrusted_graph_text_cannot_close_data_script_or_create_markup():
    value = '</script><img src=x onerror="alert(1)">&\u2028'
    graph = {"project_id": value, "nodes": [], "edges": [], "datasets": {}}
    page = render_html(graph, [{"consumer": "a", "dependency": "b"}])
    assert value not in page and "<img" not in page
    payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL)[1]
    data = json.loads(payload)
    assert data["graph"] == graph
    assert data["directions"]["supports"] == "downstream"
    assert data["dependencies"] == [{"consumer": "a", "dependency": "b"}]


def test_result_first_explorer_has_offline_inspection_and_versioned_links():
    graph = {
        "project_id": "p",
        "nodes": [
            {"id": "s", "type": "source", "natural_key": "Source", "data": {}},
            {"id": "r", "type": "indicator", "natural_key": "Coverage", "data": {
                "value": 42, "unit": "%", "scope": "2026", "review_status": "needs_review",
                "revision": 3,
            }},
        ],
        "edges": [{"src": "r", "dst": "s", "type": "depends_on", "data": {"provisional": True}}],
        "datasets": {},
    }
    page = render_html(
        graph, [], selected_ids=["r"],
        explanations={"r": {"gaps": ["Source record not resolved"], "execution": {"status": "reported"}}},
    )
    data = _payload(page)
    assert data["selected_ids"] == ["r"]
    assert data["explanations"]["r"]["gaps"] == ["Source record not resolved"]
    changed = {**graph, "nodes": [graph["nodes"][0], {**graph["nodes"][1],
        "data": {**graph["nodes"][1]["data"], "value": 43}}]}
    assert data["revisions"]["r"] != _payload(render_html(changed, []))["revisions"]["r"]
    assert 'id="results"' in page
    assert 'id="diagram"' in page
    assert 'id="linear"' in page
    assert 'id="back"' in page
    assert 'id="link-state"' in page
    assert 'location.hash' in page
    assert 'result_rev' in page
    assert 'addEventListener("hashchange"' in page
    assert 'createElementNS' in page
    assert 'Provisional' in page
    assert '<script src=' not in page and '<link ' not in page


def test_explorer_omits_source_bytes_and_bundle_code_from_embedded_data():
    graph = {"project_id": "p", "nodes": [{"id": "s", "type": "source_snapshot",
        "natural_key": "S", "data": {"content_hash": "sha256:abc", "text": "secret source bytes",
        "execution_bundle": {"code": "secret bundle code"}}}], "edges": [], "datasets": {}}
    page = render_html(graph, [], explanations={"s": {"evidence": [{"content": "secret excerpt"}]}})
    assert "secret source bytes" not in page
    assert "secret bundle code" not in page
    assert "secret excerpt" not in page
    assert _payload(page)["graph"]["nodes"][0]["data"]["content_hash"] == "sha256:abc"


def test_claim_text_is_retained_while_snapshot_text_is_omitted():
    graph = {"project_id": "p", "nodes": [
        {"id": "claim", "type": "claim", "natural_key": "Conclusion",
         "data": {"text": "Supported conclusion", "metadata": {"value": 7, "unit": "items"}}},
        {"id": "snapshot", "type": "source_snapshot", "natural_key": "Capture",
         "data": {"text": "private captured source"}},
    ], "edges": [], "datasets": {}}
    page = render_html(graph, [], selected_ids=["claim"])
    data = _payload(page)
    assert data["graph"]["nodes"][0]["data"]["text"] == "Supported conclusion"
    assert "private captured source" not in page
    assert 'n.data?.metadata' in page


def test_explorer_embeds_result_contexts_without_merging_shared_versions():
    current = {"id": "shared", "type": "dataset", "natural_key": "input",
               "data": {"description": "current"}}
    first = {"id": "first", "type": "artifact", "natural_key": "first", "data": {}}
    second = {"id": "second", "type": "artifact", "natural_key": "second", "data": {}}
    graph = {"project_id": "p", "nodes": [first, second, current], "edges": [], "datasets": {},
             "contexts": {
                 "first": {"nodes": [first, {**current, "data": {"description": "retained A"}}],
                           "edges": [], "dependencies": [], "datasets": {}},
                 "second": {"nodes": [second, {**current, "data": {"description": "retained B"}}],
                            "edges": [], "dependencies": [], "datasets": {}},
             }}
    page = render_html(graph, [], selected_ids=["first", "second"])
    payload = _payload(page)
    assert payload["graph"]["contexts"]["first"]["nodes"][1]["data"]["description"] == "retained A"
    assert payload["graph"]["contexts"]["second"]["nodes"][1]["data"]["description"] == "retained B"
    assert payload["context_revisions"]["first"]["shared"] != payload["context_revisions"]["second"]["shared"]
    assert 'contextFor' in page


def test_explorer_accepts_separate_contexts_without_changing_exported_graph():
    selected = {"id": "result", "type": "artifact", "natural_key": "Result", "data": {}}
    historical = {"id": "input", "type": "dataset", "natural_key": "Input",
                  "data": {"description": "Retained input"}}
    graph = {"project_id": "p", "nodes": [selected], "edges": [], "datasets": {}}
    contexts = {"result": {"nodes": [selected, historical], "edges": [],
                           "dependencies": [{"consumer": "result", "dependency": "input"}],
                           "datasets": {}}}
    payload = _payload(render_html(graph, [], contexts=contexts))
    assert payload["graph"] == graph
    assert payload["contexts"]["result"]["nodes"][1]["data"]["description"] == "Retained input"
    assert payload["context_revisions"]["result"]["input"]


def test_explorer_exposes_structured_result_and_selected_input_records():
    output = {"id": "result", "type": "artifact", "natural_key": "Totals", "data": {}}
    input_node = {"id": "input", "type": "dataset", "natural_key": "Rows", "data": {}}
    graph = {"project_id": "p", "nodes": [output, input_node], "edges": [], "datasets": {}}
    explanations = {"result": {"selection": {"value": {"total": 30}}, "inputs": [
        {"node_id": "input", "basis": "retained", "records": [
            {"record": {"key": "a", "data": {"value": 10}}}]}
    ]}}
    page = render_html(graph, [], explanations=explanations, selected_ids=["result"])
    payload = _payload(page)
    assert payload["explanations"]["result"]["selection"]["value"] == {"total": 30}
    assert payload["explanations"]["result"]["inputs"][0]["records"][0]["record"]["key"] == "a"
    assert "JSON.stringify(value,null,2)" in page
    assert "Retained input records" in page
    assert "entry.node_id===activeId" in page
    assert "pendingHash" in page
    assert "trail.length=0" in page


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_large_diagram_branch_plan_expands_and_pages_without_losing_nodes():
    ids = ["result", "a", "b", "shared", *(f"tail{i}" for i in range(120))]
    edges = [("result", "a"), ("result", "b"), ("a", "shared"), ("b", "shared"),
             ("shared", "a"), ("shared", "tail0"),
             *((f"tail{i}", f"tail{i + 1}") for i in range(119))]
    graph = {"project_id": "p", "nodes": [
        {"id": node_id, "type": "dataset", "natural_key": node_id, "data": {}}
        for node_id in ids
    ], "edges": [{"src": src, "dst": dst, "type": "depends_on", "data": {}}
                 for src, dst in edges], "datasets": {}}
    page = render_html(graph, [])
    assert 'id="branches"' in page and 'id="diagram-more"' in page
    assert 'role="group"' in page and 'role="img"' not in page
    script = re.search(r"<script>\n(.*?)</script>", page, re.DOTALL)[1]
    planner = re.search(
        r"(function walk\(.*?\n}\nfunction diagramPlan\(.*?\n})\nfunction ", script, re.DOTALL
    )[1]
    check = planner + f"\nconst edges={json.dumps(_payload(page)['graph']['edges'])};\n" + """
const view={pairs:edges.map(e=>({from:e.src,to:e.dst})),
 byId:new Map(['result','a','b','shared',...Array.from({length:120},(_,i)=>`tail${i}`)]
  .map(id=>[id,{id}]))};
const visit=walk('result','upstream');
if(visit.order.length!==124||new Set(visit.order).size!==124)throw Error('cycle or shared node repeated');
if(visit.branch.get('shared')!=='a'||visit.branch.get('tail119')!=='a')throw Error('branch assignment');
const shown=new Set(visit.order);
const initial=diagramPlan(visit,'result',shown,new Set(),80);
if(JSON.stringify(initial.visible)!==JSON.stringify(['result','a','b']))throw Error('collapsed branches');
const expanded=diagramPlan(visit,'result',shown,new Set(['a']),80);
if(expanded.visible.length!==80||expanded.hiddenCount!==44)throw Error('first page');
const more=diagramPlan(visit,'result',shown,new Set(['a']),160);
if(more.visible.length!==124||more.hiddenCount!==0||!more.visible.includes('tail119'))throw Error('expanded page');
"""
    result = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_selected_value_preserves_recorded_null_and_empty_structures():
    graph = {"project_id": "p", "nodes": [], "edges": [], "datasets": {}}
    script = re.search(r"<script>\n(.*?)</script>", render_html(graph, []), re.DOTALL)[1]
    functions = re.search(
        r"(function valueState\(.*?\n}\nfunction readableValue\(.*?\n})\n",
        script, re.DOTALL,
    )[1]
    check = functions + """
const cases=[
 [{value_available:true,value:null},undefined,'null'],
 [{value_available:false,value:30},30,'Not recorded'],
 [{value:{}},undefined,'{}'],
 [{value:[]},undefined,'[]'],
 [{value:false},undefined,'false'],
 [{value_available:true,value:''},undefined,'""'],
 [{value:null},undefined,'Not recorded'],
 [undefined,42,'42'],
];
for(const [selection,fallback,want] of cases){
 const got=readableValue(valueState(selection,fallback));
 if(got!==want)throw Error(`${JSON.stringify(selection)}: ${got} != ${want}`);
}
"""
    result = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_explorer_names_current_and_retained_context_explicitly():
    graph = {"project_id": "p", "nodes": [], "edges": [], "datasets": {}}
    page = render_html(graph, [])
    assert 'id="basis-state"' in page
    assert "Historical retained inputs" in page
    assert "Current graph context" in page
