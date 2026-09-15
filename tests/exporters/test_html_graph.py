import json
import re

from xyle_trace.exporters.html_graph import render_html


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
