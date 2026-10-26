"""Self-contained, offline provenance explorer. Graph strings remain inert data."""

import json
from urllib.parse import urlsplit, urlunsplit

from xyle_trace.core.revisions import revision
from xyle_trace.models.entities import EDGE_DIRECTION

# Captured source text and retained execution bundles are never export defaults.
# Keep reviewed excerpts out of the generic renderer as well: the caller may
# share reviewed references, while including source text needs a separate choice.
_OMIT_KEYS = {
    "content", "source_text", "captured_text", "source_bytes",
    "raw_bytes", "raw", "body", "response_body", "payload",
    "execution_bundle", "bundle", "bundle_code", "source_code", "script_text", "code",
    "password", "secret", "token", "access_token", "refresh_token", "api_key",
    "credential", "credentials", "authorization", "headers", "cookie", "private_key",
}


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "[omitted malformed URL]"
    if parts.scheme in {"http", "https", "ftp"} and not parts.netloc:
        return "[omitted malformed URL]"
    if not (parts.scheme or parts.netloc or parts.query or parts.fragment):
        return value
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def safe_metadata(value, *, _claim_data=False):
    """Project metadata for sharing, retaining only a claim's own prose text."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _OMIT_KEYS or (lowered == "text" and not _claim_data):
                continue
            if key == "data" and value.get("type") == "claim" and isinstance(item, dict):
                result[key] = safe_metadata(item, _claim_data=True)
            elif isinstance(item, str) and (
                lowered in {"uri", "url"} or lowered.endswith(("_uri", "_url"))
                or (lowered == "natural_key" and "://" in item)
            ):
                result[key] = _safe_url(item)
            else:
                result[key] = safe_metadata(item)
        return result
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    return value


def render_html(
    graph: dict,
    dependencies: list[dict],
    *,
    explanations: dict | None = None,
    selected_ids: list[str] | None = None,
    contexts: dict | None = None,
) -> str:
    selected_contexts = contexts if contexts is not None else graph.get("contexts", {})
    payload = json.dumps(
        safe_metadata({
            "graph": graph,
            "contexts": selected_contexts,
            "dependencies": dependencies,
            "directions": EDGE_DIRECTION,
            "explanations": explanations or {},
            "selected_ids": selected_ids,
            "revisions": {
                node["id"]: revision(node.get("data") or {})
                for node in graph.get("nodes", [])
            },
            "context_revisions": {
                result_id: {
                    node["id"]: revision(node.get("data") or {})
                    for node in context.get("nodes", [])
                }
                for result_id, context in selected_contexts.items()
            },
        }),
        ensure_ascii=True,
        sort_keys=True,
    ).replace("<", "\\u003c").replace("&", "\\u0026").replace(">", "\\u003e")
    return _PAGE.replace("__PAYLOAD__", payload)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lineage explorer</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color:#19332d;background:#f4f6f3}
*{box-sizing:border-box}body{margin:0}header,main{padding:1.2rem;max-width:1280px;margin:auto}
h1,h2,h3{line-height:1.2}h1{margin:.1rem 0}h2{margin:1.4rem 0 .5rem}
p{max-width:80ch}small,.muted{color:#4f6259}button,input,select{font:inherit}
button{border:1px solid #839a8e;background:white;color:inherit;border-radius:5px;padding:.55rem .7rem;cursor:pointer}
button:hover,button:focus-visible{outline:3px solid #287857;outline-offset:2px}
input,select{border:1px solid #839a8e;border-radius:5px;padding:.55rem;background:white;color:inherit}
label{display:inline-flex;flex-direction:column;gap:.2rem;margin:.3rem .8rem .3rem 0}
#results{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.65rem}
.result{text-align:left;display:flex;flex-direction:column;gap:.25rem;min-height:8rem}
.result[aria-current=true]{border:2px solid #176948;background:#e6f3eb}
.value{font-size:1.35rem;font-weight:700;white-space:pre-wrap;overflow-wrap:anywhere}.pill{display:inline-block;border:1px solid #a6b9ad;border-radius:20px;padding:.05rem .45rem;font-size:.8rem;margin:.15rem .25rem .15rem 0}
#layout{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(310px,1fr);gap:1rem}
article,.surface{background:white;padding:1rem;border:1px solid #d0ddd4;border-radius:5px;min-width:0}
#diagram{overflow:auto;max-height:530px;background:#fbfdfb;border:1px solid #d0ddd4}
#branches{display:flex;flex-wrap:wrap;gap:.4rem;margin:.6rem 0}
#branches:empty{display:none}#branches button[aria-expanded=true]{background:#d9f0e3}
#branches section{border:1px solid #d0ddd4;padding:.5rem;flex:1 1 220px}
#branches strong{display:block;margin-bottom:.35rem}#branches button{display:block;width:100%;margin:.25rem 0}
#diagram-more{margin:.6rem 0 0}
#diagram svg{display:block;min-width:100%}#linear{max-height:330px;overflow:auto;padding-left:1.7rem}
#linear li{padding:.2rem}#linear button{border:0;text-decoration:underline;padding:.2rem}
#details dl{display:grid;grid-template-columns:minmax(7rem,30%) 1fr;margin:.3rem 0;gap:.3rem}
#details dt{font-weight:600}#details dd{margin:0;overflow-wrap:anywhere}
#details ul{padding-left:1.3rem}#details li{margin:.2rem 0}
#details section{border-top:1px solid #dae5dc;padding:.3rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
#link-state{border-left:4px solid #9c6418;padding:.4rem .7rem;background:#fff7e8}
#link-state:empty{display:none}#status{min-height:1.5rem}
svg .link{stroke:#869e90;stroke-width:2;fill:none}svg .item{fill:#fff;stroke:#6c9180;stroke-width:1.5}
svg .active .item{fill:#d9f0e3;stroke:#176948;stroke-width:3}
svg .entry{cursor:pointer}svg .entry:focus-visible{outline:3px solid #287857}
svg text{fill:#19332d;font:14px system-ui,sans-serif}svg .kind{font-size:11px;fill:#4f6259}
@media(max-width:800px){#layout{grid-template-columns:1fr}header,main{padding:1rem}}
@media print{body{background:white}header,main{max-width:none}#controls,#back{display:none}#layout{display:block}#diagram{max-height:none;overflow:visible}article,.surface{break-inside:avoid}button{border:0}}
</style></head><body>
<header><h1>Lineage explorer</h1><p id="project"></p>
<p>Start with a result, then follow its calculation, inputs, and evidence. A connection records provenance; it does not certify that the evidence is complete or suitable.</p></header>
<main><h2>Results</h2><div id="results" aria-label="Results"></div>
<p id="link-state" role="alert"></p>
<p id="basis-state" role="status"></p>
<div id="controls"><label>Search <input id="search" type="search" placeholder="Result or node name"></label>
<label>Type <select id="type"><option value="">All types</option></select></label>
<label>Show <select id="direction"><option value="upstream">Inputs to result</option><option value="downstream">Affected outputs</option></select></label></div>
<p id="status" role="status"></p><button id="back" type="button">Back</button>
<div id="layout"><div><details class="surface" open><summary>Connected dependency diagram</summary><div id="branches" role="group" aria-label="Dependency branches"></div><div id="diagram" role="group" aria-label="Connected provenance diagram; use the linear list below for keyboard navigation"></div><button id="diagram-more" type="button" hidden>Show more nodes</button></details>
<details class="surface" open><summary>Accessible linear path</summary><ol id="linear"></ol></details></div>
<article id="details" tabindex="-1"><h2 id="heading">Select a result</h2><p id="summary"></p><div id="fields"></div><div id="explanation"></div><details><summary>Raw metadata</summary><pre id="raw"></pre></details></article></div></main>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
"use strict";
const {graph,contexts,dependencies,directions,explanations,selected_ids,revisions,context_revisions}=JSON.parse(document.getElementById("data").textContent);
const el=id=>document.getElementById(id), nodes=Array.isArray(graph.nodes)?graph.nodes:[];
const globalById=new Map(nodes.map(n=>[n.id,n]));
const edges=Array.isArray(graph.edges)?graph.edges:[];
function connections(selectedGraph,selectedDependencies){
 const pairs=[];
 for(const d of selectedDependencies||[])pairs.push({from:d.consumer,to:d.dependency,type:"record backing",data:d});
 for(const e of selectedGraph.edges||[]){
  const dir=directions[e.type];
  if(dir==="upstream")pairs.push({from:e.src,to:e.dst,type:e.type,data:e.data||{}});
  if(dir==="downstream")pairs.push({from:e.dst,to:e.src,type:e.type,data:e.data||{}});
 }
 return pairs;
}
const globalView={graph,byId:globalById,pairs:connections(graph,dependencies)};
function contextFor(result){
 const selectedGraph=contexts?.[result];
 if(!selectedGraph)return globalView;
 const byId=new Map((selectedGraph.nodes||[]).map(n=>[n.id,n]));
 if(!byId.has(result)&&globalById.has(result))byId.set(result,globalById.get(result));
 return {graph:selectedGraph,byId,pairs:connections(selectedGraph,selectedGraph.dependencies||[])};
}
let focusId=null, activeId=null;
let view=globalView;
const trail=[];
let pendingHash=null;
let branchStateKey=null, expandedBranches=new Set(), diagramLimit=80;
const label=n=>n.natural_key||n.id;
const revision=(n,result)=>String(context_revisions?.[result]?.[n.id]??revisions?.[n.id]??n.data?.revision??n.data?.content_hash??n.data?.version??n.id);
const human=s=>String(s).replaceAll("_"," ").replace(/\\b\\w/g,c=>c.toUpperCase());
const display=v=>v===undefined||v===null||v===""?"Not recorded":String(v);
const dataFor=n=>({...n.data?.metadata,...n.data});
function valueState(selection,fallback){
 if(selection&&(Object.hasOwn(selection,"value_available")||Object.hasOwn(selection,"value"))){
  const available=selection.value_available===true||
   (selection.value_available===undefined&&selection.value!==null&&selection.value!==undefined);
  return {available,value:selection.value};
 }
 return {available:fallback!==null&&fallback!==undefined,value:fallback};
}
function readableValue(state){
 if(!state.available)return "Not recorded";
 const value=state.value;
 if(value===null)return "null";
 if(value==="")return '""';
 if(typeof value==="object")return JSON.stringify(value,null,2);
 return String(value);
}
const resultValue=(n,id)=>valueState(explanations?.[id]?.selection,dataFor(n).value);
const results=(selected_ids===null?nodes.filter(n=>["indicator","claim","artifact"].includes(n.type)).map(n=>n.id):selected_ids).filter(id=>globalById.has(id));
if(!results.length)results.push(...nodes.filter(n=>n.type!=="project").slice(0,1).map(n=>n.id));
el("project").textContent=`${display(graph.project_id)} · ${nodes.length} nodes · ${edges.length} edges`;
for(const type of [...new Set(nodes.map(n=>n.type))].sort()){
 const o=document.createElement("option");o.value=type;o.textContent=human(type);el("type").append(o);
}
function pairNote(p){
 const d=p.data||{};
 const tags=[human(p.type)];
 if(d.provisional===true||d.status==="provisional")tags.push("Provisional");
 for(const key of ["qualifier","status","basis"]){if(d[key]&&d[key]!=="provisional")tags.push(String(d[key]));}
 return tags.join(" · ");
}
function walk(root,direction){
 const seen=new Set([root]),depth=new Map([[root,0]]),branch=new Map([[root,null]]),queue=[root];
 const adjacency=new Map();
 for(const p of view.pairs){
  const from=direction==="upstream"?p.from:p.to,to=direction==="upstream"?p.to:p.from;
  if(!adjacency.has(from))adjacency.set(from,[]);
  adjacency.get(from).push(to);
 }
 for(let i=0;i<queue.length;i++)for(const to of adjacency.get(queue[i])||[]){
  if(view.byId.has(to)&&!seen.has(to)){
   seen.add(to);depth.set(to,depth.get(queue[i])+1);
   branch.set(to,queue[i]===root?to:branch.get(queue[i]));queue.push(to);
  }
 }
 return {seen,depth,branch,order:queue};
}
function diagramPlan(visit,root,shown,expanded,limit){
 const branchRoots=visit.order.filter(id=>visit.branch.get(id)===id);
 const candidates=visit.order.filter(id=>shown.has(id)&&(
  id===root||visit.branch.get(id)===id||expanded.has(visit.branch.get(id))
 ));
 return {branchRoots,candidates,visible:candidates.slice(0,limit),
  hiddenCount:Math.max(0,candidates.length-limit)};
}
function linkFor(result,node){
 const selectedView=contextFor(result);
 const params=new URLSearchParams({result,result_rev:revision(selectedView.byId.get(result),result),node,rev:revision(selectedView.byId.get(node),result)});
 return "#"+params.toString();
}
function setSelection(result,node,push=true){
 if(!globalById.has(result))return;
 const selectedView=contextFor(result);
 if(!selectedView.byId.has(node))return;
 const changed=result!==focusId||node!==activeId;
 if(!changed&&location.hash===linkFor(result,node))return;
 if(push&&activeId&&changed)trail.push([focusId,activeId]);
 focusId=result;activeId=node;view=selectedView;
 const hash=linkFor(result,node);
 if(location.hash!==hash){pendingHash=hash;location.hash=hash;}
 el("link-state").textContent="";
 render();
}
function readLink(){
 if(!location.hash){
  pendingHash=null;
  if(!results.length)return false;
  trail.length=0;focusId=results[0];activeId=focusId;view=contextFor(focusId);
  el("link-state").textContent="";render();return true;
 }
 const internal=pendingHash===location.hash;
 pendingHash=null;
 const p=new URLSearchParams(location.hash.slice(1)),result=p.get("result"),node=p.get("node"),rev=p.get("rev"),resultRev=p.get("result_rev");
 const selectedView=contextFor(result);
 if(!globalById.has(result)||!selectedView.byId.has(node)||!results.includes(result)){
  el("link-state").textContent="This link points to a result or node absent from this export.";return false;
 }
 if(rev!==revision(selectedView.byId.get(node),result)||resultRev!==revision(selectedView.byId.get(result),result)){
  el("link-state").textContent="Version mismatch: the linked result or node has changed since this link was made. Inspect this export before relying on it.";
 }else el("link-state").textContent="";
 if(!internal&&focusId&&activeId&&(focusId!==result||activeId!==node)){
  const previous=trail.at(-1);
  if(previous&&previous[0]===result&&previous[1]===node)trail.pop();
  else trail.push([focusId,activeId]);
 }
 focusId=result;activeId=node;view=selectedView;render();return true;
}
function make(tag,content,cls){const x=document.createElement(tag);if(cls)x.className=cls;if(content!==undefined)x.textContent=content;return x;}
function appendValue(parent,value){
 if(Array.isArray(value)){
  if(!value.length){parent.append(make("span","Not recorded","muted"));return;}
  const list=make("ul");for(const item of value){const li=make("li");appendValue(li,item);list.append(li);}parent.append(list);return;
 }
 if(value&&typeof value==="object"){
  const dl=make("dl");for(const [key,item] of Object.entries(value)){
   dl.append(make("dt",human(key)));const dd=make("dd");appendValue(dd,item);dl.append(dd);
  }parent.append(dl);return;
 }
 parent.append(make("span",display(value)));
}
function field(parent,key,value){const dl=make("dl");dl.append(make("dt",human(key)));const dd=make("dd");appendValue(dd,value);dl.append(dd);parent.append(dl);}
function renderResults(){
 el("results").replaceChildren();
 const q=el("search").value.toLowerCase();
 for(const id of results){const n=contextFor(id).byId.get(id),data=dataFor(n);if(!JSON.stringify(n).toLowerCase().includes(q))continue;
  const b=make("button",undefined,"result");b.type="button";b.setAttribute("aria-current",String(id===focusId));
  b.append(make("strong",label(n)),make("span",`${readableValue(resultValue(n,id))} ${data.unit||""}`,"value"),
   make("small",`${human(n.type)} · Scope: ${display(data.scope||data.period)}`),
   make("small",`Review: ${display(data.review_status)} · Run: ${display(explanations?.[id]?.execution?.state||data.capture_mode||data.execution_status||data.status)}`));
  b.onclick=()=>setSelection(id,id);el("results").append(b);
 }
}
function renderBranches(visit,plan,direction){
 const box=el("branches");box.replaceChildren();
 if(visit.order.length<=80)return;
 const sizes=new Map();for(const id of visit.order.slice(1)){
  const root=visit.branch.get(id);sizes.set(root,(sizes.get(root)||0)+1);
 }
 const groups=new Map();
 for(const id of plan.branchRoots){
  const pair=view.pairs.find(p=>direction==="upstream"?p.from===focusId&&p.to===id:p.to===focusId&&p.from===id);
  const purpose=pair?.type||"dependency";
  if(!groups.has(purpose))groups.set(purpose,[]);
  groups.get(purpose).push([id,pair]);
 }
 for(const [purpose,entries] of groups){
  const group=make("section");group.append(make("strong",human(purpose)));
  for(const [id,pair] of entries){
   const b=make("button",`${label(view.byId.get(id))} · ${sizes.get(id)||1} nodes${pair?.data?.provisional?" · Provisional":""}`);
   b.type="button";b.setAttribute("aria-expanded",String(expandedBranches.has(id)));
   b.setAttribute("aria-controls","diagram");
   b.onclick=()=>{
    if(expandedBranches.has(id)){
     expandedBranches.delete(id);
     if(visit.branch.get(activeId)===id&&activeId!==id)setSelection(focusId,id);
     else render();
    }else{expandedBranches.add(id);render();}
   };
   group.append(b);
  }
  box.append(group);
 }
}
function renderDiagram(visit,order){
 const box=el("diagram");box.replaceChildren();
 if(!order.length){box.append(make("p","No nodes match the filter."));return;}
 const groups=new Map();for(const id of order){const d=visit.depth.get(id)||0;if(!groups.has(d))groups.set(d,[]);groups.get(d).push(id);}
 const levels=[...groups.keys()],width=Math.max(620,levels.length*240),height=Math.max(180,Math.max(...[...groups.values()].map(g=>g.length))*95+50);
 const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");
 svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.setAttribute("width",String(width));svg.setAttribute("height",String(height));
 const pos=new Map();for(const [depth,ids] of groups)ids.forEach((id,i)=>pos.set(id,[25+depth*240,25+i*95]));
 for(const p of view.pairs){if(!pos.has(p.from)||!pos.has(p.to))continue;
  const from=el("direction").value==="upstream"?p.from:p.to,to=el("direction").value==="upstream"?p.to:p.from;
  if(!pos.has(from)||!pos.has(to))continue;
  const line=document.createElementNS(ns,"path"),a=pos.get(from),b=pos.get(to);
  line.setAttribute("d",`M ${a[0]+195} ${a[1]+27} C ${a[0]+215} ${a[1]+27}, ${b[0]-20} ${b[1]+27}, ${b[0]} ${b[1]+27}`);
  line.setAttribute("class","link");const title=document.createElementNS(ns,"title");title.textContent=pairNote(p);line.append(title);svg.append(line);
 }
 for(const id of order){const n=view.byId.get(id),[x,y]=pos.get(id),g=document.createElementNS(ns,"g");
  g.setAttribute("class",`entry${id===activeId?" active":""}`);g.setAttribute("role","button");g.setAttribute("tabindex","0");
  g.setAttribute("aria-label",`${label(n)}, ${human(n.type)}`);
  const rect=document.createElementNS(ns,"rect");rect.setAttribute("x",String(x));rect.setAttribute("y",String(y));rect.setAttribute("width","195");rect.setAttribute("height","55");rect.setAttribute("rx","5");rect.setAttribute("class","item");g.append(rect);
  for(const [txt,dy,cls] of [[label(n).slice(0,25),23,""],[human(n.type),42,"kind"]]){
   const t=document.createElementNS(ns,"text");t.setAttribute("x",String(x+9));t.setAttribute("y",String(y+dy));if(cls)t.setAttribute("class",cls);t.textContent=txt;g.append(t);
  }
  const title=document.createElementNS(ns,"title");title.textContent=label(n);g.append(title);
  g.onclick=()=>setSelection(focusId,id);
  g.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setSelection(focusId,id);}};
  svg.append(g);
 }
 box.append(svg);
}
function renderLinear(visit,shown){
 const list=el("linear");list.replaceChildren();
 for(const id of visit.order){if(!shown.has(id))continue;const n=view.byId.get(id),li=make("li"),b=make("button",`${label(n)} (${human(n.type)})`);
  b.type="button";b.onclick=()=>setSelection(focusId,id);li.append(b);
  const incoming=view.pairs.filter(p=>p.from===id&&visit.seen.has(p.to));
  if(incoming.length)li.append(make("small",incoming.map(pairNote).join("; ")));
  list.append(li);
 }
}
function renderDetails(){
 const n=view.byId.get(activeId);if(!n)return;
 el("heading").textContent=label(n);el("summary").textContent=`${human(n.type)} · ID ${n.id} · Version ${revision(n,focusId)}`;
 const fields=el("fields");fields.replaceChildren();
 const data=dataFor(n);
 const value=activeId===focusId?resultValue(n,focusId):null;
 for(const key of ["text","definition","description","value","unit","scope","period","review_status","status","capture_mode","reproducible","replay_of","execution_status","formula","code_reference","content_hash","uri","retrieval_url","locator","retrieved_at","created_at"]){
  if(key==="value"&&value){field(fields,key,readableValue(value));continue;}
  if(data[key]!==undefined)field(fields,key,data[key]);
 }
 if(!fields.childNodes.length)fields.append(make("p","No descriptive metadata recorded.","muted"));
 const detail=el("explanation");detail.replaceChildren();
 const exp=explanations?.[focusId];
 const input=Array.isArray(exp?.inputs)?exp.inputs.find(entry=>entry.node_id===activeId):null;
 if(input){
  const section=make("section");section.append(make("h3",input.basis==="retained"?"Retained input records":"Input records"));
  field(section,"basis",input.basis);
  if(input.records?.length)appendValue(section,input.records);
  else section.append(make("p","No retained input records are available for this node.","muted"));
  detail.append(section);
 }
 if(exp){for(const key of ["selection","calculations","inputs","evidence","assumptions","decisions","gaps","execution"]){
  const section=make("section");section.append(make("h3",human(key)));appendValue(section,exp[key]);detail.append(section);
 }}else detail.append(make("p","Explanation not available for this result."));
 const related=view.pairs.filter(p=>p.from===activeId||p.to===activeId);
 const section=make("section");section.append(make("h3","Connections"));
 if(!related.length)section.append(make("p","No connected provenance recorded."));
 else{const ul=make("ul");for(const p of related){const li=make("li",`${pairNote(p)}: ${label(view.byId.get(p.from)||{id:p.from})} → ${label(view.byId.get(p.to)||{id:p.to})}`);ul.append(li);}section.append(ul);}
 detail.append(section);
 el("raw").textContent=JSON.stringify({node:n,connections:related,dataset:view.graph.datasets?.[activeId]||null},null,2);
}
function render(){
 renderResults();if(!focusId||!activeId)return;
 const exp=explanations?.[focusId];
 el("basis-state").textContent=exp?.graph_basis==="retained_inputs"||exp?.selection?.historical===true?
  "Historical retained inputs for this selected run":
  exp?.graph_basis==="current_context"||exp?.selection?.historical===false?
  "Current graph context; exact historical inputs may be unavailable":
  "Context basis not recorded";
 const direction=el("direction").value,visit=walk(focusId,direction),type=el("type").value,q=el("search").value.toLowerCase();
 const shown=new Set(visit.order.filter(id=>{const n=view.byId.get(id);return (!type||n.type===type)&&JSON.stringify(n).toLowerCase().includes(q);}));
 shown.add(focusId);
 const key=`${focusId}|${direction}`;
 if(key!==branchStateKey){
  branchStateKey=key;diagramLimit=80;
  const roots=diagramPlan(visit,focusId,shown,new Set(),80).branchRoots;
  expandedBranches=new Set(visit.order.length<=80?roots:[]);
 }
 const activeBranch=visit.branch.get(activeId);
 if(activeBranch&&activeId!==activeBranch)expandedBranches.add(activeBranch);
 let plan=diagramPlan(visit,focusId,shown,expandedBranches,diagramLimit);
 const activePosition=plan.candidates.indexOf(activeId);
 if(activePosition>=diagramLimit){
  diagramLimit=Math.ceil((activePosition+1)/80)*80;
  plan=diagramPlan(visit,focusId,shown,expandedBranches,diagramLimit);
 }
 el("status").textContent=`${plan.visible.length} diagram nodes shown · ${shown.size} of ${visit.order.length} connected nodes match · ${label(view.byId.get(focusId))}`;
 el("back").disabled=trail.length===0;
 renderBranches(visit,plan,direction);renderDiagram(visit,plan.visible);
 const more=el("diagram-more");more.hidden=plan.hiddenCount===0;
 more.textContent=`Show next ${Math.min(80,plan.hiddenCount)} nodes`;
 renderLinear(visit,shown);renderDetails();
}
el("back").onclick=()=>{const prior=trail.pop();if(prior)setSelection(prior[0],prior[1],false);};
el("diagram-more").onclick=()=>{diagramLimit+=80;render();};
for(const id of ["search","type","direction"])el(id).addEventListener("input",render);
window.addEventListener("hashchange",readLink);
if(!readLink()&&results.length){focusId=results[0];activeId=focusId;view=contextFor(focusId);render();}
else if(!results.length){el("status").textContent="No results in this export.";}
</script></body></html>
"""
