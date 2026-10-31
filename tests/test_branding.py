"""Branding must remain portable, accessible, and independent of graph input."""

import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from importlib.resources import files
from urllib.parse import unquote

import pytest

from xyle_trace.exporters.html_graph import render_html
from xyle_trace.exporters.report import render_report


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "link" or (tag in {"img", "script"} and "src" in data):
            self.resources.append((tag, data))


@pytest.mark.parametrize(
    "name",
    [
        "logo.svg",
        "logo-dark.svg",
        "mark.svg",
        "mark-dark.svg",
        "mark-mono.svg",
        "favicon.svg",
        "social-preview.svg",
        "icon-square.svg",
    ],
)
def test_brand_assets_are_accessible_vector_art_without_embedded_or_remote_resources(name):
    asset = files("xyle_trace").joinpath("assets", "brand", name).read_text(encoding="utf-8")
    root = ET.fromstring(asset)
    assert root.get("viewBox") and root.get("aria-label", "").startswith("Xyle Trace")
    assert "<title>" in asset or name == "social-preview.svg"
    for element in root.iter():
        assert element.tag.rsplit("}", 1)[-1] not in {
            "script",
            "image",
            "foreignObject",
            "metadata",
        }
        assert not any(key.lower().startswith("on") for key in element.attrib)
        assert not any(key.rsplit("}", 1)[-1] in {"href", "src"} for key in element.attrib)
    assert "url(" not in asset and "@import" not in asset and "@font-face" not in asset


@pytest.mark.parametrize("name", ["logo", "mark"])
def test_inverse_assets_preserve_the_approved_geometry(name):
    def geometry(suffix):
        root = ET.fromstring(
            files("xyle_trace")
            .joinpath("assets", "brand", f"{name}{suffix}.svg")
            .read_text(encoding="utf-8")
        )
        return root.get("viewBox"), [
            (p.get("d"), p.get("transform")) for p in root if p.tag.endswith("path")
        ]

    assert geometry("") == geometry("-dark")


def test_offline_views_embed_artwork_and_a_local_theme_aware_favicon():
    graph = {
        "project_id": "p",
        "nodes": [
            {"id": "result", "type": "indicator", "natural_key": "Result", "data": {"value": 42}}
        ],
        "edges": [],
        "datasets": {},
    }
    for page in [render_html(graph, []), render_report(graph, [], selected_ids=["result"])]:
        assert 'class="xyle-brand"' in page and 'aria-label="Xyle Trace"' in page
        resources = Resources()
        resources.feed(page)
        assert len(resources.resources) == 1
        tag, data = resources.resources[0]
        assert tag == "link" and data["rel"] == "icon"
        assert data["href"].startswith("data:image/svg+xml,")
        favicon = unquote(data["href"].split(",", 1)[1])
        assert "prefers-color-scheme:dark" in favicon
        assert ET.fromstring(favicon).get("aria-label") == "Xyle Trace mark"


def test_untrusted_text_that_resembles_brand_template_markers_stays_inert():
    value = "__XYLE_BRAND__ __XYLE_FAVICON__ __PAYLOAD__"
    graph = {"project_id": value, "nodes": [], "edges": [], "datasets": {}}
    page = render_html(graph, [])
    payload = re.search(
        r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL
    )[1]
    assert json.loads(payload)["graph"] == graph
    assert page.count('class="xyle-brand"') == 1
