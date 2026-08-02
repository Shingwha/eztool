"""Provider implementations. Importing this package registers them.

To add a new service: drop a module here that subclasses Provider with
@register, then add it to the imports below.
"""
from __future__ import annotations

from . import firecrawl, jina_reader, markdown_new  # noqa: F401  (side-effect: register)

__all__ = ["firecrawl", "jina_reader", "markdown_new"]
