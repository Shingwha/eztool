"""Keen（Keenable）后端：web 搜索（POST /v1/search）+ page 抓取（GET /v1/fetch）。

Keenable 是面向 AI agent 的搜索/抓取 API：搜索返回标题/URL/摘要/长摘录，
fetch 端点返回页面 markdown（默认走索引副本，live=true 实时抓源站）。

认证自动切换（同一参数面，只换路径和 header）：
- 配置了 providers.keen.api_key → /v1/search、/v1/fetch + X-API-Key（正式额度）
- 未配置 → /v1/search/public、/v1/fetch/public + X-Keenable-Title（必填，
  keyless 免费池：每 IP 1000 次/小时、10 次/秒，不耗额度）
"""

from __future__ import annotations

import json
import time
import urllib.parse

from ..provider import (
    CATEGORY_HTTP,
    Provider,
    SearchResponse,
    SearchResult,
    ServiceError,
    post_json,
    register,
)
from ..util import NoResultsError

API_BASE = "https://api.keenable.ai"
DEFAULT_TIMEOUT = 30
APP_TITLE = "eztool"  # public 端点必填的应用标识（X-Keenable-Title）


@register
class KeenProvider(Provider):
    name = "keen"
    categories = frozenset({"web", "page"})
    # keyless 免费兜底（公共池限流）；配了 key 走正式额度
    config = {
        "api_key": {"secret": True, "hint": "Keenable API key (falls back to keyless public pool when unset)"},
        "timeout": {"default": 30, "hint": "keen request timeout in seconds"},
    }
    priority = {"web": 40, "page": 60}  # 匿名兜底，排在各默认链末尾

    def _auth_headers(self) -> dict:
        if self.api_key:
            return {"X-API-Key": self.api_key}
        return {"X-Keenable-Title": APP_TITLE}

    def _path(self, kind: str) -> str:
        """kind: search / fetch；按凭证切换 public 孪生路径。"""
        suffix = "" if self.api_key else "/public"
        return f"{API_BASE}/v1/{kind}{suffix}"

    def has_credentials(self) -> bool:
        return True  # keyless 兜底，永远可用

    def test_credentials(self) -> str:
        t0 = time.monotonic()
        data = self._search("test", self.timeout(DEFAULT_TIMEOUT))
        elapsed = time.monotonic() - t0
        mode = "keyless" if not self.api_key else "api_key"
        n = len(data.get("results") or [])
        return f"OK ({mode}, {n} results, {elapsed:.1f}s)"

    def _search(self, query: str, timeout: int) -> dict:
        status, _hdrs, raw = post_json(
            self._path("search"), self._auth_headers(), {"query": query}, timeout
        )
        if status != 200:
            raise ServiceError(f"keen returned HTTP {status}", CATEGORY_HTTP, http_code=status)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ServiceError(f"keen: invalid JSON response: {e}", CATEGORY_HTTP) from None

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        # 注意：/v1/search 无条数参数（count 不适用），结果数由服务端决定
        data = self._search(query.strip(), self.timeout(DEFAULT_TIMEOUT))

        results = [
            SearchResult(
                title=str(r.get("title") or r.get("url") or "Untitled"),
                url=str(r.get("url") or ""),
                snippet=str(r.get("description") or ""),
                content=r.get("snippet") or None,  # 长摘录
                extra={"published_at": r.get("published_at")} if r.get("published_at") else None,
            )
            for r in (data.get("results") or [])
        ]
        if not results:
            raise NoResultsError("keen: no results found")

        return SearchResponse(
            query=query.strip(),
            results=results,
            metadata={"total_results": len(results)},
        )

    # ── page 抓取能力 ──

    def build_headers(self) -> dict:
        return self._auth_headers()

    def build_target(self, url: str) -> str:
        # live=true：直接抓源站（默认只支持已索引 URL，未索引会报错）
        qs = urllib.parse.urlencode({"url": url, "live": "true"})
        return f"{self._path('fetch')}?{qs}"

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"keen fetch returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"keen fetch: invalid JSON: {e}", CATEGORY_HTTP) from None
        content = data.get("content") or ""
        if not content.strip():
            raise ServiceError("keen fetch: no body text returned", CATEGORY_HTTP)
        return content
