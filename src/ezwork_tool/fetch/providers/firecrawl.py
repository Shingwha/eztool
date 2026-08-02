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
import urllib.error
import urllib.request

from ..provider import CATEGORY_HTTP, FetchError, Provider, register

API_URL = "https://api.firecrawl.dev/v2/scrape"


@register
class FirecrawlProvider(Provider):
    name = "firecrawl"

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
        req = urllib.request.Request(
            API_URL, data=payload, headers=self.build_headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            # enrich the error with the API's own message (e.g. 402
            # "Payment required", 429 rate limit)
            detail = ""
            try:
                info = json.loads(e.read().decode("utf-8", "replace"))
                msg = info.get("error") or info.get("message")
                if msg:
                    detail = f": {msg}"
            except Exception:
                pass
            raise FetchError(f"HTTP {e.code}{detail}", CATEGORY_HTTP, e.code) from e
        except FetchError:
            raise
        except Exception as e:
            raise self._map_error(e, timeout) from e

    def parse_body(self, status: int, headers, body: bytes) -> str:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise FetchError(
                f"{self.name}: invalid JSON response: {e}", CATEGORY_HTTP
            ) from e
        if not data.get("success", True) and data.get("error"):
            raise FetchError(
                f"{self.name} API error: {data['error']}", CATEGORY_HTTP
            )
        md = (data.get("data") or {}).get("markdown") or ""
        return md
