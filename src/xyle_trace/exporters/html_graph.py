"""An offline explorer. Graph text is data, never interpolated HTML or JavaScript."""

import json

from xyle_trace.models.entities import EDGE_DIRECTION


def render_html(graph: dict, dependencies: list[dict]) -> str:
    payload = json.dumps(
        {"graph": graph, "dependencies": dependencies, "directions": EDGE_DIRECTION},
        ensure_ascii=True,
        sort_keys=True,
    ).replace("<", "\\u003c").replace("&", "\\u0026")
    return _PAGE.replace("__PAYLOAD__", payload)


_PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lineage explorer</title>
<style>
:root{font:16px system-ui;color:#19332d;background:#f4f6f3}
body{margin:0}header,main{padding:1.5rem;max-width:1200px;margin:auto}
h1{margin:.2rem 0}p{line-height:1.5}label{display:inline-block;margin:0 1rem 1rem 0}
input,select,button{font:inherit;padding:.5rem;border:1px solid #81958c;border-radius:4px}
button{background:white;color:inherit;cursor:pointer;text-align:left}
button[aria-pressed=true]{background:#d2ece0}#layout{display:grid;grid-template-columns:1fr 2fr;gap:1rem}
#nodes{display:flex;flex-direction:column;gap:.5rem;max-height:70vh;overflow:auto}
article{background:white;padding:1rem;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
small{display:block;color:#53685f}#status{min-height:1.5rem}
@media(max-width:700px){#layout{grid-template-columns:1fr}#nodes{max-height:30vh}}
</style>
<header><h1>Lineage explorer</h1><p id="project"></p>
<p>Inspect captured evidence, record backing, and review status. Provisional and
historical paths remain visible; reachability does not certify support or reproducibility.</p></header>
<main><label>Search <input id="search" type="search" placeholder="Name, ID, or metadata"></label>
<label>Type <select id="type"><option value="">All types</option></select></label>
<label>Show <select id="direction"><option value="all">All nodes</option>
<option value="upstream">Upstream of selection</option>
<option value="downstream">Downstream of selection</option></select></label>
<p id="status" role="status"></p><div id="layout"><nav id="nodes" aria-label="Nodes"></nav>
<article><h2 id="heading">Select a node</h2><pre id="details"></pre></article></div></main>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
"use strict";
const {graph,dependencies,directions}=JSON.parse(document.getElementById("data").textContent);
const el=id=>document.getElementById(id), byId=new Map(graph.nodes.map(n=>[n.id,n]));
let selected=null;
el("project").textContent=`${graph.project_id} · ${graph.nodes.length} nodes · ${graph.edges.length} edges`;
for(const type of [...new Set(graph.nodes.map(n=>n.type))].sort()){
 const option=document.createElement("option");option.value=type;option.textContent=type;el("type").append(option);
}
const pairs=dependencies.map(d=>[d.consumer,d.dependency]);
for(const edge of graph.edges){
 const direction=directions[edge.type];
 if(direction==="upstream")pairs.push([edge.src,edge.dst]);
 if(direction==="downstream")pairs.push([edge.dst,edge.src]);
}
function reachable(id,direction){
 const seen=new Set([id]),queue=[id];
 for(let i=0;i<queue.length;i++)for(const pair of pairs){
  const [from,to]=direction==="upstream"?pair:[pair[1],pair[0]];
  if(from===queue[i]&&!seen.has(to)){seen.add(to);queue.push(to);}
 }
 return seen;
}
function render(){
 const allowed=selected&&el("direction").value!=="all"?reachable(selected,el("direction").value):null;
 const query=el("search").value.toLowerCase(),type=el("type").value;
 const visible=graph.nodes.filter(n=>(!allowed||allowed.has(n.id))&&(!type||n.type===type)&&JSON.stringify(n).toLowerCase().includes(query));
 el("nodes").replaceChildren();
 for(const node of visible){
  const button=document.createElement("button");button.textContent=node.natural_key;
  button.setAttribute("aria-pressed",String(node.id===selected));
  const label=document.createElement("small");label.textContent=node.type;button.append(label);
  button.onclick=()=>{selected=node.id;render();};el("nodes").append(button);
 }
 el("status").textContent=`${visible.length} nodes shown`+(selected?` · Selected: ${byId.get(selected).natural_key}`:"");
 if(selected){
  const node=byId.get(selected);el("heading").textContent=node.natural_key;
  el("details").textContent=JSON.stringify({node,dataset:graph.datasets[selected]||null,
   edges:graph.edges.filter(e=>e.src===selected||e.dst===selected),
   dependencies:dependencies.filter(d=>d.consumer===selected||d.dependency===selected)},null,2);
 }
}
for(const id of ["search","type","direction"])el(id).addEventListener("input",render);
render();
</script></html>
"""
