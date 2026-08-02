"""markdown.new provider — Cloudflare's URL→Markdown service.

Endpoint: https://markdown.new/<url>
No API key required. Supports ?method=auto|ai|browser (default auto).
"""
from __future__ import annotations

from ..provider import Provider, register


@register
class MarkdownNewProvider(Provider):
    name = "markdown"
    base_url = "https://markdown.new/"

    # Note: markdown.new treats the whole request path+query as the target
    # URL, so we cannot append ?method= without corrupting URLs that carry
    # their own query string. method (auto|ai|browser) is therefore left at
    # the service default (auto, its built-in 3-tier fallback).

