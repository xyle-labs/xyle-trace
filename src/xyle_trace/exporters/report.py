"""Curated, printable HTML report for explicitly selected provenance results."""

import hashlib
import re
from html import escape

from xyle_trace.core.revisions import revision
from xyle_trace.exporters.html_graph import safe_metadata
from xyle_trace.models.entities import EDGE_DIRECTION


def _text(value) -> str:
    if value is None or value == "":
        return "Not recorded"
    return escape(str(value))


def _human(value) -> str:
    return str(value).replace("_", " ").capitalize()


def _render_value(value) -> str:
    if isinstance(value, dict):
        if not value:
            return '<span class="muted">Not recorded</span>'
        return "<dl>" + "".join(
            f"<dt>{_text(_human(key))}</dt><dd>{_render_value(item)}</dd>"
            for key, item in value.items()
        ) + "</dl>"
    if isinstance(value, list):
        if not value:
            return '<span class="muted">Not recorded</span>'
        return "<ul>" + "".join(f"<li>{_render_value(item)}</li>" for item in value) + "</ul>"
    return _text(value)


def _revision(node: dict) -> str:
    recorded = node.get("revision")
    if isinstance(recorded, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", recorded):
        return recorded
    data = node.get("data") or {}
    return revision(data)


def _anchor(node: dict) -> str:
    material = f"{node['id']}|{_revision(node)}".encode()
    return "result-" + hashlib.sha256(material).hexdigest()[:16]


def _connections(graph: dict, dependencies: list[dict]) -> list[dict]:
    pairs = [{"from": d["consumer"], "to": d["dependency"], "type": "record backing", "data": d}
             for d in dependencies]
    for edge in graph.get("edges", []):
        direction = EDGE_DIRECTION.get(edge.get("type"))
        if direction == "upstream":
            src, dst = edge["src"], edge["dst"]
        elif direction == "downstream":
            src, dst = edge["dst"], edge["src"]
        else:
            continue
        pairs.append({"from": src, "to": dst, "type": edge["type"], "data": edge.get("data") or {}})
    return pairs


def _qualifier(pair: dict) -> str:
    data = pair["data"]
    labels = [_human(pair["type"])]
    if data.get("provisional") is True or data.get("status") == "provisional":
        labels.append("Provisional")
    for key in ("qualifier", "status", "basis"):
        if data.get(key) and data[key] != "provisional":
            labels.append(str(data[key]))
    return " · ".join(labels)


def _upstream(root: str, pairs: list[dict], by_id: dict) -> list[str]:
    seen = {root}
    order = [root]
    for current in order:
        for pair in pairs:
            neighbour = pair["to"]
            if pair["from"] == current and neighbour in by_id and neighbour not in seen:
                seen.add(neighbour)
                order.append(neighbour)
    return order


def _display_data(node: dict) -> dict:
    data = node.get("data") or {}
    metadata = data.get("metadata")
    return {**(metadata if isinstance(metadata, dict) else {}), **data}


def _node_summary(node: dict, data: dict | None = None) -> str:
    data = data if data is not None else _display_data(node)
    fields = ("text", "definition", "description", "value", "unit", "scope", "period",
              "review_status", "status",
              "capture_mode", "reproducible", "replay_of", "execution_status",
              "formula", "code_reference", "content_hash", "uri", "retrieval_url",
              "locator", "retrieved_at", "created_at")
    rows = "".join(
        f"<dt>{_text(_human(key))}</dt><dd>{_render_value(_human(data[key]) if key in {'review_status', 'status', 'capture_mode', 'execution_status'} else data[key])}</dd>"
        for key in fields if key in data
    )
    return f"<dl>{rows}</dl>" if rows else '<p class="muted">No descriptive metadata recorded.</p>'


def render_report(
    graph: dict,
    dependencies: list[dict],
    *,
    selected_ids: list[str],
    explanations: dict | None = None,
    title: str = "Provenance report",
) -> str:
    """Render only selected results and their upstream closure; never fetch content."""
    if not selected_ids:
        raise ValueError("At least one selected result is required")
    graph = safe_metadata(graph)
    explanations = safe_metadata(explanations or {})
    by_id = {node["id"]: node for node in graph.get("nodes", [])}
    missing = [node_id for node_id in selected_ids if node_id not in by_id]
    if missing:
        raise ValueError(f"Selected result absent from graph: {', '.join(missing)}")
    sections = []
    for selected_id in dict.fromkeys(selected_ids):
        context = graph.get("contexts", {}).get(selected_id)
        if context is None:
            context = graph
            context_dependencies = dependencies
        else:
            context_dependencies = context.get("dependencies", [])
        local_by_id = {node["id"]: node for node in context.get("nodes", [])}
        local_by_id.setdefault(selected_id, by_id[selected_id])
        result = local_by_id[selected_id]
        pairs = _connections(context, context_dependencies)
        included = _upstream(selected_id, pairs, local_by_id)
        included_set = set(included)
        related = [p for p in pairs if p["from"] in included_set and p["to"] in included_set]
        exp = explanations.get(selected_id)
        data = _display_data(result)
        selection = exp.get("selection") if isinstance(exp, dict) else None
        if isinstance(selection, dict) and "value" in selection:
            data["value"] = selection["value"]
        execution = exp.get("execution") if isinstance(exp, dict) else None
        run_state = execution.get("state") if isinstance(execution, dict) else None
        run_state = run_state or data.get("capture_mode") or data.get("execution_status") or data.get("status")
        review_status = _human(data["review_status"]) if data.get("review_status") else None
        name = result.get("natural_key") or selected_id
        section = [f'<article id="{_anchor(by_id[selected_id])}"><header><p class="eyebrow">{_text(_human(result.get("type", "result")))}</p>',
                   f'<h2>{_text(name)}</h2>',
                   f'<p class="value">{_text(data.get("value"))} {_text(data.get("unit")) if data.get("unit") else ""}</p>',
                   (f'<p>Scope: {_text(data.get("scope") or data.get("period"))} · '
                    f'Review: {_text(review_status)} · Run: {_text(run_state)}</p>'),
                   f'<p class="muted">Node {_text(selected_id)} · Version {_text(_revision(result))}</p></header>']
        section.append('<h3>Result details</h3>' + _node_summary(result, data))
        if exp:
            for key in ("selection", "calculations", "inputs", "evidence", "assumptions", "decisions", "gaps", "execution"):
                value = exp.get(key)
                message = "Evidence not recorded" if key == "evidence" else "Not recorded"
                section.append(f'<section><h3>{_text(_human(key))}</h3>'
                               + (_render_value(value) if value else f'<p class="muted">{message}</p>')
                               + '</section>')
        else:
            section.append('<p class="notice">Explanation not available for this result. Evidence not recorded in this report.</p>')
        section.append('<section><h3>Provenance path</h3>')
        if related:
            section.append('<ol class="path">')
            for pair in related:
                source, target = local_by_id[pair["from"]], local_by_id[pair["to"]]
                section.append(f'<li><strong>{_text(source.get("natural_key") or source["id"])}</strong> '
                               f'→ {_text(target.get("natural_key") or target["id"])} '
                               f'<span class="tag">{_text(_qualifier(pair))}</span></li>')
            section.append('</ol>')
        else:
            section.append('<p class="notice">No upstream connection recorded.</p>')
        section.append('</section><section><h3>Source and method details</h3><ol class="nodes">')
        for node_id in included[1:]:
            node = local_by_id[node_id]
            section.append(f'<li><strong>{_text(node.get("natural_key") or node_id)}</strong> '
                           f'<span class="tag">{_text(_human(node.get("type", "node")))}</span>'
                           f'<p class="muted">Node {_text(node_id)} · Version {_text(_revision(node))}</p>'
                           f'{_node_summary(node)}</li>')
        section.append('</ol></section><p class="muted">Captured source text and retained execution bundle code are omitted. '
                       'A missing detail means it was not included in this report.</p></article>')
        sections.append("".join(section))
    heading = _text(title)
    links = "".join(
        f'<li><a href="#{_anchor(by_id[node_id])}">{_text(by_id[node_id].get("natural_key") or node_id)}</a></li>'
        for node_id in dict.fromkeys(selected_ids)
    )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{heading}</title>
<style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#19332d;background:#f4f6f3}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:950px;margin:auto;padding:2rem}}
h1,h2,h3{{line-height:1.2}}h1{{margin:.2rem 0}}article{{background:white;border:1px solid #cedbd1;padding:1.5rem;margin:2rem 0;break-inside:avoid}}
article section{{border-top:1px solid #dbe7de;padding:.4rem 0}}p{{overflow-wrap:anywhere}}
.value{{font-size:1.4rem;font-weight:700}}.muted,.eyebrow{{color:#50645b}}.eyebrow{{text-transform:uppercase;letter-spacing:.08em;font-size:.8rem}}
.notice{{padding:.6rem;border-left:4px solid #a76c21;background:#fff6e8}}
dl{{display:grid;grid-template-columns:minmax(8rem,27%) 1fr;gap:.25rem;margin:.3rem 0}}
dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}li{{margin:.35rem 0;overflow-wrap:anywhere}}
.tag{{display:inline-block;border:1px solid #b5c9ba;border-radius:20px;padding:.05rem .4rem;font-size:.85rem}}
a{{color:#135d40}}a:focus-visible{{outline:3px solid #287857}}
@media(max-width:600px){{main{{padding:1rem}}article{{padding:1rem}}dl{{grid-template-columns:1fr}}}}
@media print{{:root{{background:white}}main{{max-width:none;padding:0}}article{{border:0;border-top:2px solid #19332d;margin:1rem 0;page-break-inside:auto}}a{{color:inherit;text-decoration:none}}}}
</style></head><body><main><header><h1>{heading}</h1><p>Project {_text(graph.get("project_id"))}. Selected results and recorded upstream provenance.</p>
<p>Generated from this export. Review status, edge qualifiers, and omissions remain part of the interpretation.</p></header>
<nav aria-label="Report results"><h2>Contents</h2><ol>{links}</ol></nav>{''.join(sections)}</main></body></html>'''
