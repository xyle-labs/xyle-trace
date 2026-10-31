"""Trusted, packaged brand artwork for self-contained offline HTML exports."""

from functools import lru_cache
from importlib.resources import files
from urllib.parse import quote


@lru_cache(maxsize=1)
def brand_header() -> str:
    logo = (
        files("xyle_trace")
        .joinpath("assets", "brand", "logo.svg")
        .read_text(encoding="utf-8")
        .strip()
    )
    return f'<div class="xyle-brand">{logo}</div>'


@lru_cache(maxsize=1)
def favicon_link() -> str:
    mark = (
        files("xyle_trace")
        .joinpath("assets", "brand", "favicon.svg")
        .read_text(encoding="utf-8")
        .strip()
    )
    return (
        f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(mark, safe="")}">'
    )
