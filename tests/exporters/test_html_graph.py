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


def record_view(current_value, retained_value):
    result = {"id": "result", "type": "artifact", "data": {}}
    dataset = {"id": "input", "type": "dataset", "data": {"description": "Stable metadata"}}
    graph = {"project_id": "p", "nodes": [result, dataset], "edges": [],
             "datasets": {"input": {"records": [{"key": "A", "data": {"value": current_value}}]}}}
    contexts = {"result": {"nodes": [result, dataset], "edges": [], "dependencies": [],
                           "datasets": {}}}
    explanations = {"result": {"graph_basis": "retained_inputs", "inputs": [
        {"node_id": "input", "records": [{"record": {"key": "A", "data": {
            "value": retained_value}}}]}]}}
    return render_html(graph, [], contexts=contexts, explanations=explanations,
                       selected_ids=["result"])


def test_view_checksums_cover_records_when_node_metadata_is_unchanged():
    original = _payload(record_view(10, 10))
    changed = _payload(record_view(12, 11))
    assert original["revisions"] == changed["revisions"]
    assert original["context_revisions"] == changed["context_revisions"]
    assert original["view_revision"] != changed["view_revision"]
    assert original["contexts"]["result"]["view_revision"] != changed["contexts"]["result"]["view_revision"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_links_pin_current_and_retained_record_views():
    pages = [record_view(10, 10), record_view(12, 11)]
    script = re.search(r"<script>\n(.*?)</script>", pages[0], re.DOTALL)[1]
    functions = "\n".join(re.search(pattern, script, re.DOTALL)[1] for pattern in (
        r"(function contextFor\(.*?\n})\n", r"(function selectionView\(.*?\n})\n",
        r"(const revision=.*?;)\n", r"(function linkFor\(.*?\n})\n",
        r"(function readLink\(.*?\n})\n"))
    check = "const payloads=" + json.dumps([_payload(page) for page in pages]) + ";\n"
    check += "const links=[]; for(const payload of payloads){\n" + """
const {graph,contexts,explanations,revisions,context_revisions,view_revision}=payload;
const globalById=new Map(graph.nodes.map(n=>[n.id,n]));
const globalView={basis:'current',byId:globalById,contextRevision:view_revision};
const results=['result'],location={hash:''},trail=[];
const controls={'link-state':{textContent:''},direction:{value:'upstream'}};
const el=id=>controls[id];
let focusId=null,activeId=null,activeDirection='upstream',impactRootId=null,pendingHash=null,view=globalView;
function connections(){return [];}
function render(){}
""" + functions + "\n" + """
const currentLink=linkFor('result','input','downstream','input');
const retainedLink=linkFor('result','input','upstream',null);
const current=new URLSearchParams(currentLink.slice(1));
const retained=new URLSearchParams(retainedLink.slice(1));
if(current.get('context_rev')!==view_revision)throw Error('current view is not pinned');
if(retained.get('context_rev')!==contexts.result.view_revision)throw Error('retained view is not pinned');
if(links.length){for(const oldLink of links[0]){
 location.hash=oldLink;focusId=null;activeId=null;pendingHash=null;
 controls['link-state'].textContent='';
 if(!readLink()||!controls['link-state'].textContent.includes('version mismatch'))
  throw Error('stale record context was silently accepted');
}}
links.push([currentLink,retainedLink]);
}
if(links[0][0]===links[1][0]||links[0][1]===links[1][1])throw Error('record changes kept stale links');
"""
    result = subprocess.run(["node", "-e", check], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'p.get("context_rev")!==' in script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_downstream_uses_current_graph_while_upstream_keeps_retained_inputs():
    result = {"id": "result", "type": "artifact", "natural_key": "Result", "data": {}}
    current_output = {"id": "current-output", "type": "claim", "natural_key": "Current", "data": {}}
    current_input = {"id": "input", "type": "dataset", "natural_key": "Input",
                     "data": {"description": "current version"}}
    retained_input = {**current_input, "data": {"description": "retained version"}}
    graph = {"project_id": "p", "nodes": [result, current_output, current_input],
             "edges": [{"src": "current-output", "dst": "result", "type": "depends_on", "data": {}}],
             "datasets": {}}
    contexts = {"result": {"nodes": [result, retained_input], "edges": [
        {"src": "result", "dst": "input", "type": "depends_on", "data": {}}],
        "dependencies": [], "datasets": {}}}
    page = render_html(graph, [], contexts=contexts, selected_ids=["result"],
                       explanations={"result": {"graph_basis": "retained_inputs"}})
    script = re.search(r"<script>\n(.*?)</script>", page, re.DOTALL)[1]
    selector = re.search(r"(function selectionView\(.*?\n})\n", script, re.DOTALL)
    assert selector, "a direction-aware context selector is required"
    walk = re.search(r"(function walk\(.*?\n})\n", script, re.DOTALL)[1]
    link = re.search(r"(function linkFor\(.*?\n})\n", script, re.DOTALL)[1]
    select = re.search(r"(function setSelection\(.*?\n})\n", script, re.DOTALL)[1]
    revision = re.search(r"(const revision=.*?;)\n", script)[1]
    payload = _payload(page)
    check = f"const graph={json.dumps(payload['graph'])};\n" + f"const contexts={json.dumps(payload['contexts'])};\n" + f"const revisions={json.dumps(payload['revisions'])};\n" + f"const context_revisions={json.dumps(payload['context_revisions'])};\n" + """
const globalView={basis:'current',byId:new Map(graph.nodes.map(n=>[n.id,n])),
 pairs:graph.edges.map(e=>({from:e.src,to:e.dst}))};
function contextFor(id){const g=contexts[id];return {basis:'historical',
 byId:new Map(g.nodes.map(n=>[n.id,n])),pairs:g.edges.map(e=>({from:e.src,to:e.dst}))};}
let view=globalView;
let activeDirection='upstream',impactRootId=null;
const globalById=globalView.byId,location={hash:''},trail=[];
const controls={direction:{value:'upstream'},'link-state':{textContent:''}};
const el=id=>controls[id];
let focusId=null,activeId=null,pendingHash=null;
function render(){}
""" + selector[1] + "\n" + revision + "\n" + walk + "\n" + link + "\n" + select + "\n" + """
view=selectionView('result','upstream');
if(view.basis!=='historical'||view.byId.get('input').data.description!=='retained version')
 throw Error('upstream lost retained input');
if(JSON.stringify(walk('result','upstream').order)!==JSON.stringify(['result','input']))
 throw Error('retained upstream path changed');
view=selectionView('result','downstream');
if(view.basis!=='current'||view.byId.get('input').data.description!=='current version')
 throw Error('downstream did not use current graph');
if(JSON.stringify(walk('result','downstream').order)!==JSON.stringify(['result','current-output']))
 throw Error('current downstream output missing');
const retainedLink=new URLSearchParams(linkFor('result','input','upstream').slice(1));
const currentLink=new URLSearchParams(linkFor('result','input','downstream').slice(1));
if(retainedLink.get('basis')!=='historical'||retainedLink.get('direction')!=='upstream')
 throw Error('retained link lacks basis and direction');
if(currentLink.get('basis')!=='current'||currentLink.get('direction')!=='downstream')
 throw Error('current link lacks basis and direction');
if(retainedLink.get('rev')===currentLink.get('rev'))throw Error('input versions were mixed');
if(retainedLink.get('rev')!==context_revisions.result.input||currentLink.get('rev')!==revisions.input)
 throw Error('link used a revision from the wrong graph');
setSelection('result','input',true,'upstream');
setSelection('result','result',true,'downstream');
if(trail.length!==1||trail[0][1]!=='input'||trail[0][2]!=='upstream')
 throw Error('direction switch lost retained selection for Back');
if(view.basis!=='current'||controls.direction.value!=='downstream')
 throw Error('direction switch did not select current view');
const prior=trail.pop();setSelection(prior[0],prior[1],false,prior[2],prior[3]);
if(view.basis!=='historical'||activeId!=='input'||controls.direction.value!=='upstream')
 throw Error('Back did not restore retained version');
if(new URLSearchParams(location.hash.slice(1)).get('rev')!==context_revisions.result.input)
 throw Error('Back linked to the wrong input version');
"""
    result = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'p.get("basis")!==selectedView.basis' in script
    assert 'trail.push([focusId,activeId,activeDirection,impactRootId])' in script
    assert 'setSelection(prior[0],prior[1],false,prior[2],prior[3])' in script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_context_basis_uses_explanation_and_unknown_context_keeps_its_revision():
    current = {"id": "result", "type": "indicator", "natural_key": "Result",
               "data": {"description": "current"}}
    contextual = {**current, "data": {"description": "contextual"}}
    graph = {"project_id": "p", "nodes": [current], "edges": [], "datasets": {}}
    contexts = {"result": {"nodes": [contextual], "edges": [], "dependencies": [],
                           "datasets": {}}}
    page = render_html(graph, [], contexts=contexts, selected_ids=["result"])
    script = re.search(r"<script>\n(.*?)</script>", page, re.DOTALL)[1]
    context = re.search(r"(function contextFor\(.*?\n})\n", script, re.DOTALL)[1]
    selector = re.search(r"(function selectionView\(.*?\n})\n", script, re.DOTALL)[1]
    revision = re.search(r"(const revision=.*?;)\n", script)[1]
    payload = _payload(page)
    check = f"const graph={json.dumps(payload['graph'])};\n" + f"const contexts={json.dumps(payload['contexts'])};\n" + f"const revisions={json.dumps(payload['revisions'])};\n" + f"const context_revisions={json.dumps(payload['context_revisions'])};\n" + """
const globalById=new Map(graph.nodes.map(n=>[n.id,n]));
const globalView={graph,byId:globalById,pairs:[],basis:'current'};
function connections(){return [];}
let explanations={result:{graph_basis:'current_context',selection:{historical:false}}};
""" + context + "\n" + selector + "\n" + revision + "\n" + """
if(selectionView('result','upstream').basis!=='current')
 throw Error('reported context mislabeled historical');
if(selectionView('result','downstream')!==globalView)
 throw Error('downstream must use current global graph');
explanations={result:{graph_basis:'retained_inputs',selection:{historical:true}}};
if(selectionView('result','upstream').basis!=='historical')
 throw Error('retained context mislabeled current');
explanations={};
const unknown=selectionView('result','upstream');
if(unknown.basis!=='unknown')throw Error('unknown context asserted a basis');
if(revision(unknown.byId.get('result'),'result',unknown.basis)!==context_revisions.result.result)
 throw Error('unknown context lost its per-result revision');
if(revision(globalById.get('result'),'result','current')!==revisions.result)
 throw Error('current view used contextual revision');
"""
    result = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Context basis not recorded" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_selected_input_can_anchor_current_impacts_without_losing_retained_back_path():
    result = {"id": "result", "type": "artifact", "natural_key": "Result", "data": {}}
    current_input = {"id": "input", "type": "dataset", "natural_key": "Input",
                     "data": {"description": "current"}}
    retained_input = {**current_input, "data": {"description": "retained"}}
    outputs = [{"id": key, "type": "claim", "natural_key": key, "data": {}}
               for key in ("affected-a", "affected-b")]
    graph = {"project_id": "p", "nodes": [result, current_input, *outputs],
             "edges": [{"src": item["id"], "dst": "input", "type": "depends_on", "data": {}}
                       for item in outputs], "datasets": {}}
    contexts = {"result": {"nodes": [result, retained_input], "edges": [
        {"src": "result", "dst": "input", "type": "depends_on", "data": {}}],
        "dependencies": [], "datasets": {}}}
    page = render_html(graph, [], contexts=contexts, selected_ids=["result"],
                       explanations={"result": {"graph_basis": "retained_inputs"}})
    script = re.search(r"<script>\n(.*?)</script>", page, re.DOTALL)[1]
    assert "impactRootId" in script, "downstream navigation needs a stable selected input root"
    functions = [re.search(rf"(function {name}\(.*?\n}})\n", script, re.DOTALL)[1]
                 for name in ("contextFor", "selectionView", "walk", "linkFor",
                              "setSelection", "changeDirection")]
    revision = re.search(r"(const revision=.*?;)\n", script)[1]
    payload = _payload(page)
    check = f"const graph={json.dumps(payload['graph'])};\n" + f"const contexts={json.dumps(payload['contexts'])};\n" + f"const revisions={json.dumps(payload['revisions'])};\n" + f"const context_revisions={json.dumps(payload['context_revisions'])};\n" + """
const explanations={result:{graph_basis:'retained_inputs'}};
const globalById=new Map(graph.nodes.map(n=>[n.id,n]));
function connections(g){return g.edges.map(e=>({from:e.src,to:e.dst}));}
const globalView={graph,byId:globalById,pairs:connections(graph),basis:'current'};
const location={hash:''},trail=[];
const controls={direction:{value:'upstream'},'link-state':{textContent:''}};
const el=id=>controls[id];
let focusId=null,activeId=null,activeDirection='upstream',impactRootId=null;
let view=globalView,pendingHash=null;
function render(){}
""" + revision + "\n" + "\n".join(functions) + "\n" + """
setSelection('result','input',true,'upstream',null);
if(view.basis!=='historical')throw Error('upstream input is not retained');
changeDirection('downstream');
if(view.basis!=='current'||impactRootId!=='input')throw Error('current impact root not selected');
if(JSON.stringify(walk(impactRootId,'downstream').order)!==
   JSON.stringify(['input','affected-a','affected-b']))
 throw Error('two current affected results missing');
const anchored=new URLSearchParams(location.hash.slice(1));
if(anchored.get('impact')!=='input'||anchored.get('impact_rev')!==revisions.input||
   anchored.get('basis')!=='current')throw Error('impact link is not pinned to current input');
setSelection('result','affected-a');
if(impactRootId!=='input'||walk(impactRootId,'downstream').order.length!==3)
 throw Error('click moved the impact anchor');
const prior=trail.pop();setSelection(prior[0],prior[1],false,prior[2],prior[3]);
if(impactRootId!=='input'||activeId!=='input')throw Error('Back lost current impact root');
changeDirection('upstream');
if(view.basis!=='historical'||activeId!=='result'||impactRootId!==null)
 throw Error('Inputs to result did not restore selected retained result');
const impact=trail.pop();setSelection(impact[0],impact[1],false,impact[2],impact[3]);
if(view.basis!=='current'||impactRootId!=='input')
 throw Error('Back did not restore current impact root after direction switch');
const earlier=trail.pop();setSelection(earlier[0],earlier[1],false,earlier[2],earlier[3]);
if(view.basis!=='historical'||activeId!=='input'||impactRootId!==null)
 throw Error('Back did not restore retained input inspection');
if(new URLSearchParams(location.hash.slice(1)).get('rev')!==context_revisions.result.input)
 throw Error('retained input revision lost after Back');
"""
    result = subprocess.run(["node", "-e", check], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'p.get("impact")' in script and 'p.get("impact_rev")' in script
