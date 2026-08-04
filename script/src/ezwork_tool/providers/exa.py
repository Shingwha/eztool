"""Exa 后端：search.web（POST /search）+ convert.page（POST /contents）。

Exa 是 neural 语义搜索引擎（原 Metaphor）：搜索结果质量高，支持 category
（news/company/publication/people…）、includeDomains 定向、type 多档
（instant→deep-reasoning）、contents 一次附带全文。

认证：x-api-key header（config providers.exa.api_key，必须配置）。
免费：注册送 $20，之后每月送 $10；Search $7/1k 请求（≤10 结果），
Contents $1/1k 页/内容类型。默认不开 contents 省钱，需要正文走 convert.page。
"""

from __future__ import annotations

import json
import time

from ..base import Provider, SearchResponse, SearchResult
from ..errors import CATEGORY_HTTP, NoResultsError, ServiceError
from ..http import http_post
from ..registry import register

API_BASE = "https://api.exa.ai"
SEARCH_URL = f"{API_BASE}/search"
CONTENTS_URL = f"{API_BASE}/contents"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RESULTS = 10
API_MAX_RESULTS = 100


def _api_key(cfg: dict) -> str | None:
    return cfg.get("providers", {}).get("exa", {}).get("api_key")


def _post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    if not api_key:
        raise ServiceError("exa: 需要配置 providers.exa.api_key", CATEGORY_HTTP)
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    status, _hdrs, raw = http_post(
        url, headers, json.dumps(body).encode("utf-8"), int(timeout)
    )
    if status == 402:
        raise ServiceError("exa: 402 Payment Required（免费额度用尽）", CATEGORY_HTTP, http_code=402)
    if status != 200:
        raise ServiceError(f"exa returned HTTP {status}", CATEGORY_HTTP, http_code=status)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"exa: invalid JSON response: {e}", CATEGORY_HTTP) from None


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
        "numResults": count,
        "type": "auto",  # 精简：固定 auto 档（默认均衡）
    }

    data = _post_json(SEARCH_URL, body, api_key, timeout)

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
        raise NoResultsError("exa: 未找到结果")

    return SearchResponse(
        query=query.strip(),
        results=results,
        metadata={
            "total_results": len(results),
            "cost_dollars": (data.get("costDollars") or {}).get("total"),
        },
    )


@register
class ExaProvider(Provider):
    name = "exa"
    categories = frozenset({"search.web", "convert.page"})
    # 无 provider 特有参数（与 doubao/anysearch/deepseek 一致）
    category_params = {}

    def has_credentials(self, cfg: dict) -> bool:
        return bool(_api_key(cfg))

    def test_credentials(self, cfg: dict) -> str:
        t0 = time.monotonic()
        data = _post_json(
            SEARCH_URL, {"query": "test", "numResults": 1}, _api_key(cfg), DEFAULT_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        n = len(data.get("results") or [])
        return f"OK ({n} results, {elapsed:.1f}s)"

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)

    # ── convert.page 能力 ──

    def build_headers(self) -> dict:
        if not self.api_key:
            raise ServiceError("exa: 需要配置 providers.exa.api_key", CATEGORY_HTTP)
        return {"Content-Type": "application/json", "x-api-key": self.api_key}

    def _request(self, target: str, timeout: int):
        body = {"urls": [target], "text": {"maxCharacters": 10000}}
        return http_post(
            CONTENTS_URL, self.build_headers(), json.dumps(body).encode("utf-8"), timeout
        )

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"exa contents returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"exa contents: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("text"):
            raise ServiceError("exa contents: 未取到正文", CATEGORY_HTTP)
        return results[0]["text"]
