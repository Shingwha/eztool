"""Firecrawl provider — POST https://api.firecrawl.dev/v2/scrape.

Firecrawl is a dedicated scraping service: browser rendering, smart
caching, boilerplate removal, clean markdown out of the box. **No API
key required** — keyless access works with per-IP rate limits. Set
``providers.firecrawl.api_key`` in the config for higher limits.

No content limits are imposed — neither client-side truncation nor a
server-side ``maxContentLength`` (the v2 API has no such parameter), so
the full page markdown comes back untouched.
"""
from __future__ import annotations

import json

from ..provider import Provider, post_json, register
from ..util import CATEGORY_HTTP, ServiceError

API_URL = "https://api.firecrawl.dev/v2/scrape"


@register
class FirecrawlProvider(Provider):
    name = "firecrawl"
    categories = frozenset({"page"})
    # keyless 可用（per-IP 限流）；配了 key 提额度
    config = {
        "api_key": {"secret": True, "hint": "Firecrawl API key (optional; keyless access is rate-limited)"},
        "timeout": {"default": 60, "hint": "firecrawl timeout in seconds"},
    }
    priority = {"page": 40}

    def build_headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, target: str, timeout: int):
        payload = {
            "url": target,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": max(1000, min(int(timeout) * 1000, 300000)),
        }
        # map_http_error 会尽力带上 API 自己的错误消息（如 402 Payment required / 429 rate limit）
        return post_json(API_URL, self.build_headers(), payload, timeout)

    def parse_body(self, status: int, headers, body: bytes) -> str:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(
                f"{self.name}: invalid JSON response: {e}", CATEGORY_HTTP
            ) from e
        if not data.get("success", True) and data.get("error"):
            raise ServiceError(
                f"{self.name} API error: {data['error']}", CATEGORY_HTTP
            )
        md = (data.get("data") or {}).get("markdown") or ""
        return md
