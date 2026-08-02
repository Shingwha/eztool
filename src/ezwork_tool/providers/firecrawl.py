"""Firecrawl provider — POST https://api.firecrawl.dev/v2/scrape.

Firecrawl is a dedicated scraping service: browser rendering, smart
caching, boilerplate removal, clean markdown out of the box. **No API
key required** — keyless access works with per-IP rate limits. Set
``FIRECRAWL_API_KEY`` (env) or ``api_key`` in the ``[firecrawl]`` config
section for higher limits.

No content limits are imposed — neither client-side truncation nor a
server-side ``maxContentLength`` (the v2 API has no such parameter), so
the full page markdown comes back untouched.
"""
from __future__ import annotations

import json

from ..base import Provider
from ..errors import CATEGORY_HTTP, ServiceError
from ..http import http_post
from ..registry import register

API_URL = "https://api.firecrawl.dev/v2/scrape"


@register
class FirecrawlProvider(Provider):
    name = "firecrawl"
    capabilities = frozenset({"fetch"})

    def build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, target: str, timeout: int):
        payload = json.dumps(
            {
                "url": target,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "timeout": max(1000, min(int(timeout) * 1000, 300000)),
            }
        ).encode("utf-8")
        # map_http_error 会尽力带上 API 自己的错误消息（如 402 Payment required / 429 rate limit）
        return http_post(API_URL, self.build_headers(), payload, timeout)

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
