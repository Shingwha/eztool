"""Jina Reader provider — r.jina.ai.

Endpoint: https://r.jina.ai/<url>
Works without an API key (rate-limited ~20 RPM); set JINA_API_KEY (or
api_key in the config file) for higher limits.

Note: in some networks (e.g. mainland China) jina.ai domains are
unreachable — the fallback chain will time out and move on.
"""
from __future__ import annotations

from ..provider import Provider, register


@register
class JinaReaderProvider(Provider):
    name = "jina"
    base_url = "https://r.jina.ai/"

    def build_headers(self) -> dict:
        headers = super().build_headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
