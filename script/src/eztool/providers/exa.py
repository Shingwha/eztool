"""Exa 后端：web 搜索（POST /search）+ page 抓取（POST /contents）。

Exa 是 neural 语义搜索引擎（原 Metaphor）：搜索结果质量高，支持 category
（news/company/publication/people…）、includeDomains 定向、type 多档
（instant→deep-reasoning）、contents 一次附带全文。

认证：x-api-key header（config providers.exa.api_key，必须配置）。
免费：注册送 $20，之后每月送 $10；Search $7/1k 请求（≤10 结果），
Contents $1/1k 页/内容类型。默认不开 contents 省钱，需要正文走 page 抓取。
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

API_BASE = "https://api.exa.ai"
SEARCH_URL = f"{API_BASE}/search"
CONTENTS_URL = f"{API_BASE}/contents"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RESULTS = 20
API_MAX_RESULTS = 100


@register
class ExaProvider(Provider):
    name = "exa"
    categories = frozenset({"web", "page"})
    # 必须配 key；不声明 priority → 不进默认链（--use exa 或配置链使用）
    config = {
        "api_key": {"secret": True, "hint": "Exa API key (https://dashboard.exa.ai)"},
        "timeout": {"default": 30, "hint": "exa request timeout in seconds"},
    }
    auth_required = True

    def _post(self, url: str, body: dict, timeout: int) -> dict:
        """POST JSON 并解析响应；402 = 免费额度用尽（语义映射保留）。"""
        if not self.api_key:
            raise ServiceError("exa: providers.exa.api_key is required", CATEGORY_HTTP)
        try:
            _status, _hdrs, raw = post_json(
                url, {"x-api-key": self.api_key}, body, timeout
            )
        except ServiceError as e:
            if e.http_code == 402:
                raise ServiceError(
                    "exa: 402 Payment Required (free quota exhausted)",
                    CATEGORY_HTTP,
                    http_code=402,
                ) from None
            raise
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ServiceError(f"exa: invalid JSON response: {e}", CATEGORY_HTTP) from None

    def test_credentials(self) -> str:
        t0 = time.monotonic()
        data = self._post(
            SEARCH_URL, {"query": "test", "numResults": 1}, self.timeout(DEFAULT_TIMEOUT)
        )
        elapsed = time.monotonic() - t0
        n = len(data.get("results") or [])
        return f"OK ({n} results, {elapsed:.1f}s)"

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        try:
            count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
        except (TypeError, ValueError):
            count = DEFAULT_MAX_RESULTS
        count = max(1, min(count, API_MAX_RESULTS))

        body: dict = {
            "query": query.strip(),
            "numResults": count,
            "type": "auto",  # 精简：固定 auto 档（默认均衡）
        }

        data = self._post(SEARCH_URL, body, self.timeout(DEFAULT_TIMEOUT))

        results = []
        for r in (data.get("results") or []):
            text = r.get("text") or ""
            hl = r.get("highlights") or []
            snippet = "\n".join(hl) if hl else text[:300]
            results.append(
                SearchResult(
                    title=str(r.get("title") or "Untitled"),
                    url=str(r.get("url") or r.get("id") or ""),
                    snippet=snippet,
                    content=text or None,
                )
            )
        if not results:
            raise NoResultsError("exa: no results found")

        return SearchResponse(
            query=query.strip(),
            results=results,
            metadata={
                "total_results": len(results),
                "cost_dollars": (data.get("costDollars") or {}).get("total"),
            },
        )

    # ── page 抓取能力 ──

    def build_headers(self) -> dict:
        if not self.api_key:
            raise ServiceError("exa: providers.exa.api_key is required", CATEGORY_HTTP)
        return {"Content-Type": "application/json", "x-api-key": self.api_key}

    def _request(self, target: str, timeout: int):
        body = {"urls": [target], "text": {"maxCharacters": 10000}}
        return post_json(CONTENTS_URL, self.build_headers(), body, timeout)

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"exa contents returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"exa contents: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("text"):
            raise ServiceError("exa contents: no body text returned", CATEGORY_HTTP)
        return results[0]["text"]
