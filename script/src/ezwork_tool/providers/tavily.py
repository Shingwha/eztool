"""Tavily 后端：search.web（POST /search）+ convert.page（POST /extract）。

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

from ..provider import Provider, SearchResponse, SearchResult
from ..util import CATEGORY_HTTP, NoResultsError, ServiceError
from ..util import http_post
from ..provider import register

API_BASE = "https://api.tavily.com"
SEARCH_URL = f"{API_BASE}/search"
EXTRACT_URL = f"{API_BASE}/extract"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RESULTS = 20
API_MAX_RESULTS = 20


def _api_key(cfg: dict) -> str | None:
    return cfg.get("providers", {}).get("tavily", {}).get("api_key")


def _post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    """POST JSON 到 Tavily，认证自动切换 keyless / api_key。"""
    if api_key:
        body["api_key"] = api_key
    headers = {"Content-Type": "application/json"}
    if not api_key:
        headers["X-Tavily-Access-Mode"] = "keyless"
    status, _hdrs, raw = http_post(
        url, headers, json.dumps(body).encode("utf-8"), int(timeout)
    )
    if status != 200:
        raise ServiceError(f"tavily returned HTTP {status}", CATEGORY_HTTP, http_code=status)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"tavily: invalid JSON response: {e}", CATEGORY_HTTP) from None


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    api_key = _api_key(cfg)

    try:
        count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
    except (TypeError, ValueError):
        count = DEFAULT_MAX_RESULTS
    count = max(1, min(count, API_MAX_RESULTS))

    try:
        timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    body: dict = {
        "query": query.strip(),
        "max_results": count,
        "search_depth": "basic",  # 精简：固定 basic（1 credit/次）
    }

    data = _post_json(SEARCH_URL, body, api_key, timeout)

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
        raise NoResultsError("tavily: 未找到结果")

    return SearchResponse(
        query=query.strip(),
        results=results,
        answer=data.get("answer"),
        metadata={"total_results": len(results)},
    )


@register
class TavilyProvider(Provider):
    name = "tavily"
    categories = frozenset({"search.web", "convert.page"})
    # keyless 免费兜底（限流）；配了 key 走正式额度
    config = {
        "api_key": {"secret": True, "hint": "Tavily API Key（不配则自动走 keyless 免费模式）"},
        "timeout": {"default": 30, "hint": "tavily 请求超时秒数"},
    }
    priority = {"convert.page": 40}
    # search.web 不声明 priority → 不进默认搜索链（--providers tavily 显式指定）
    # 无 provider 特有参数（与 doubao/anysearch/deepseek 一致）
    params = {}

    def has_credentials(self, cfg: dict) -> bool:
        return True  # keyless 兜底，永远可用

    def test_credentials(self, cfg: dict) -> str:
        t0 = time.monotonic()
        data = _post_json(
            SEARCH_URL, {"query": "test", "max_results": 1}, _api_key(cfg), DEFAULT_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        mode = "keyless" if not _api_key(cfg) else "api_key"
        n = len(data.get("results") or [])
        return f"OK ({mode}, {n} results, {elapsed:.1f}s)"

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)

    # ── convert.page 能力（fetch 风格，参考 firecrawl.py）──

    def build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if not self.api_key:
            headers["X-Tavily-Access-Mode"] = "keyless"
        return headers

    def _request(self, target: str, timeout: int):
        body = {"urls": [target]}
        if self.api_key:
            body["api_key"] = self.api_key
        return http_post(
            EXTRACT_URL, self.build_headers(), json.dumps(body).encode("utf-8"), timeout
        )

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"tavily extract returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"tavily extract: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("raw_content"):
            raise ServiceError("tavily extract: 未取到正文", CATEGORY_HTTP)
        return results[0]["raw_content"]
