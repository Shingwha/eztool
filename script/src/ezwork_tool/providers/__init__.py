"""Provider implementations. Importing this package registers them.

To add a new service: drop a module here that subclasses Provider with
@register, then add it to the imports below.
"""
from __future__ import annotations

from . import (  # noqa: F401  (side-effect: register)
    anysearch,
    arxiv,
    crossref,
    deepseek,
    doubao,
    exa,          # ← 新增
    firecrawl,
    jina_reader,
    markdown_new,
    mineru,
    openalex,
    anydoc,
    tavily,       # ← 新增
)

__all__ = [
    "anysearch", "arxiv", "crossref", "deepseek", "doubao",
    "exa", "firecrawl", "jina_reader", "markdown_new", "mineru",
    "openalex", "anydoc", "tavily",
]
