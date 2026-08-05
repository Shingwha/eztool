"""Jina Reader provider — r.jina.ai.

Endpoint: https://r.jina.ai/<url>
Works without an API key (rate-limited ~20 RPM); set JINA_API_KEY (or
api_key in the config file) for higher limits.

Note: in some networks (e.g. mainland China) jina.ai domains are
unreachable — the fallback chain will time out and move on.
"""
from __future__ import annotations

from ..provider import Provider
from ..provider import register


@register
class JinaReaderProvider(Provider):
    name = "jina_reader"
    categories = frozenset({"convert.page"})
    base_url = "https://r.jina.ai/"
    # 匿名可用（限流）；配了 key 提额度
    config = {
        "api_key": {"secret": True, "hint": "Jina API Key（可选，匿名限流可用）"},
        "timeout": {"default": 10, "hint": "jina 超时秒数"},
    }
    priority = {"convert.page": 20}

    def build_headers(self) -> dict:
        headers = super().build_headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
