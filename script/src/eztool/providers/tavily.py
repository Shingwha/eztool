"""Tavily 后端：web 搜索（POST /search）+ page 抓取（POST /extract）。

Tavily 是面向 AI agent 的实时搜索 API：结果自带分块正文（content），
extract 端点抓任意 URL 正文（markdown，实测可过 mp.weixin.qq.com 验证墙）。

认证自动切换：
- 配置了 providers.tavily.api_key → 写入请求体 api_key 字段（正式额度）
- 未配置 → 自动加 X-Tavily-Access-Mode: keyless（限速免费，无需注册）

计费：basic/fast/ultra-fast 搜索 = 1 credit/次，advanced = 2 credits/次；
extract basic = 5 URL/credit。免费 1000 credits/月。
"""

from __future__ import annotations

import json
import time

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

API_BASE = "https://api.tavily.com"
SEARCH_URL = f"{API_BASE}/search"
EXTRACT_URL = f"{API_BASE}/extract"
DEFAULT_TIMEOUT = 30
API_MAX_RESULTS = 20  # 显式 --count 时的上限


@register
class TavilyProvider(Provider):
    name = "tavily"
    categories = frozenset({"web", "page"})
    # keyless 免费兜底（限流）；配了 key 走正式额度
    config = {
        "api_key": {"secret": True, "hint": "Tavily API key (falls back to keyless free mode when unset)"},
        "timeout": {"default": 30, "hint": "tavily request timeout in seconds"},
    }
    priority = {"web": 10, "page": 20}

    def _post_json(self, url: str, body: dict, timeout: int) -> dict:
        """POST JSON 到 Tavily，认证自动切换 keyless / api_key。"""
        if self.api_key:
            body["api_key"] = self.api_key
        headers = {} if self.api_key else {"X-Tavily-Access-Mode": "keyless"}
        status, _hdrs, raw = post_json(url, headers, body, timeout)
        if status != 200:
            raise ServiceError(f"tavily returned HTTP {status}", CATEGORY_HTTP, http_code=status)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ServiceError(f"tavily: invalid JSON response: {e}", CATEGORY_HTTP) from None

    def has_credentials(self) -> bool:
        return True  # keyless 兜底，永远可用

    def test_credentials(self) -> str:
        t0 = time.monotonic()
        data = self._post_json(
            SEARCH_URL, {"query": "test", "max_results": 1}, self.timeout(DEFAULT_TIMEOUT)
        )
        elapsed = time.monotonic() - t0
        mode = "keyless" if not self.api_key else "api_key"
        n = len(data.get("results") or [])
        return f"OK ({mode}, {n} results, {elapsed:.1f}s)"

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        # 不传 max_results → 服务端默认（tavily 默认 5 条）；--count 显式覆盖
        body: dict = {
            "query": query.strip(),
            "search_depth": "basic",  # 精简：固定 basic（1 credit/次）
        }
        count = opts.get("count")
        if count is not None:
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = None
            if count is not None:
                body["max_results"] = max(1, min(count, API_MAX_RESULTS))

        data = self._post_json(SEARCH_URL, body, self.timeout(DEFAULT_TIMEOUT))

        results = [
            SearchResult(
                title=str(r.get("title") or r.get("url", "Untitled")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content") or "")[:300],
                content=r.get("raw_content") or r.get("content"),
            )
            for r in (data.get("results") or [])
        ]
        if not results:
            raise NoResultsError("tavily: no results found")

        return SearchResponse(
            query=query.strip(),
            results=results,
            answer=data.get("answer"),
            metadata={"total_results": len(results)},
        )

    # ── page 能力（fetch 风格，参考 firecrawl.py）──

    def build_headers(self) -> dict:
        if self.api_key:
            return {}
        return {"X-Tavily-Access-Mode": "keyless"}

    def _request(self, target: str, timeout: int):
        body = {"urls": [target]}
        if self.api_key:
            body["api_key"] = self.api_key
        return post_json(EXTRACT_URL, self.build_headers(), body, timeout)

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"tavily extract returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"tavily extract: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("raw_content"):
            raise ServiceError("tavily extract: no body text returned", CATEGORY_HTTP)
        return results[0]["raw_content"]
