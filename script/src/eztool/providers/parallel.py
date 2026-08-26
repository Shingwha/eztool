"""Parallel 后端：web 搜索（POST /v1/search）+ page 抓取（POST /v1/extract）。

Parallel Web Systems 的 Search/Extract API，专为 LLM 消费设计：搜索以
natural-language objective + keyword queries 驱动，返回带长摘录（excerpts）
的结果；extract 返回 URL 的 markdown 正文（full_content，默认关，需显式开）。

认证：x-api-key header（config providers.parallel.api_key，必须配置，
https://platform.parallel.ai 申请）。搜索固定 fast 档（1s 延迟预算内高质量；
turbo 更快更糙，advanced 更慢更精）。
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

API_BASE = "https://api.parallel.ai"
SEARCH_URL = f"{API_BASE}/v1/search"
EXTRACT_URL = f"{API_BASE}/v1/extract"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RESULTS = 20


@register
class ParallelProvider(Provider):
    name = "parallel"
    categories = frozenset({"web", "page"})
    # 必须配 key；不声明 priority → 不进默认链（--use parallel 或配置链使用）
    config = {
        "api_key": {"secret": True, "hint": "Parallel API key (https://platform.parallel.ai)"},
        "timeout": {"default": 30, "hint": "parallel request timeout in seconds"},
    }
    auth_required = True

    def _post(self, url: str, body: dict, timeout: int) -> dict:
        """POST JSON 并解析响应。"""
        if not self.api_key:
            raise ServiceError(
                "parallel: providers.parallel.api_key is required", CATEGORY_HTTP
            )
        status, _hdrs, raw = post_json(url, {"x-api-key": self.api_key}, body, timeout)
        if status != 200:
            raise ServiceError(
                f"parallel returned HTTP {status}", CATEGORY_HTTP, http_code=status
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ServiceError(
                f"parallel: invalid JSON response: {e}", CATEGORY_HTTP
            ) from None

    def test_credentials(self) -> str:
        t0 = time.monotonic()
        data = self._post(
            SEARCH_URL,
            {"search_queries": ["test"], "advanced_settings": {"max_results": 1}},
            self.timeout(DEFAULT_TIMEOUT),
        )
        elapsed = time.monotonic() - t0
        n = len(data.get("results") or [])
        return f"OK ({n} results, {elapsed:.1f}s)"

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        try:
            count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
        except (TypeError, ValueError):
            count = DEFAULT_MAX_RESULTS
        count = max(1, count)  # API 默认 10，上限未文档化

        q = query.strip()
        body: dict = {
            "objective": q,
            "search_queries": [q],
            "mode": "fast",  # 精简：固定 fast 档（默认均衡）
            "advanced_settings": {"max_results": count},
        }

        data = self._post(SEARCH_URL, body, self.timeout(DEFAULT_TIMEOUT))

        results = []
        for r in (data.get("results") or []):
            excerpts = r.get("excerpts") or []
            results.append(
                SearchResult(
                    title=str(r.get("title") or r.get("url") or "Untitled"),
                    url=str(r.get("url") or ""),
                    snippet="\n".join(excerpts)[:300],
                    content="\n\n".join(excerpts) or None,
                    extra={"publish_date": r.get("publish_date")}
                    if r.get("publish_date")
                    else None,
                )
            )
        if not results:
            raise NoResultsError("parallel: no results found")

        return SearchResponse(
            query=q,
            results=results,
            metadata={
                "total_results": len(results),
                "search_id": data.get("search_id"),
            },
        )

    # ── page 抓取能力 ──

    def _request(self, target: str, timeout: int):
        if not self.api_key:
            raise ServiceError(
                "parallel: providers.parallel.api_key is required", CATEGORY_HTTP
            )
        body = {
            "urls": [target],
            "advanced_settings": {"full_content": True},  # 默认只给摘录，抓取要正文
        }
        return post_json(EXTRACT_URL, {"x-api-key": self.api_key}, body, timeout)

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"parallel extract returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"parallel extract: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if results:
            r = results[0]
            text = r.get("full_content") or "\n\n".join(r.get("excerpts") or [])
            if text.strip():
                return text
        errors = data.get("errors") or []
        detail = f": {errors[0].get('content')}" if errors else ""
        raise ServiceError(f"parallel extract: no body text returned{detail}", CATEGORY_HTTP)
