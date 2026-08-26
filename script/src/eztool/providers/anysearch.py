"""AnySearch 后端（搜索 + URL 提取）。

- 搜索：AnySearch REST API（POST /v1/search），支持匿名（无 API Key）与
  Bearer Token 鉴权。
- URL 提取（page 类别）：MCP JSON-RPC 通道（POST /mcp 的 tools/call，
  ``extract`` 工具），匿名可用；限制：仅 HTML（PDF/二进制报错）、内容
  50,000 字符截断、服务端 30s 超时——故不进默认 page 链，``--use anysearch``
  显式指定时使用。

纯标准库实现。
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..provider import (
    FetchResult,
    Provider,
    SearchResponse,
    SearchResult,
    post_json,
    register,
)
from ..util import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    NoResultsError,
    ServiceError,
)

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.anysearch.com"
MCP_ENDPOINT = "https://api.anysearch.com/mcp"  # MCP JSON-RPC 通道（extract 等工具）
DEFAULT_MAX_RESULTS = 20  # 与 config 声明的默认值一致
API_MAX_RESULTS = 20  # API 上限
DEFAULT_TIMEOUT = 60

# HTTP 状态码 → 语义码（原 core.py 映射，保留）
_ERROR_CODES: dict[int, str] = {
    400: "invalid_request",
    401: "invalid_api_key",
    402: "quota_exhausted",
    403: "forbidden",
    429: "rate_limit_exceeded",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
    504: "timeout",
}


# ── 核心 API 调用（原 core.py 逻辑，SearchError → ServiceError）──────────────


def _call_api(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """调用 AnySearch 统一搜索 API，返回解析后的顶层 JSON dict。

    Raises:
        ServiceError: 空 query / 网络错误 / API 错误（code 保留原语义码）。
    """
    if not query or not query.strip():
        raise ServiceError("Search query is empty.", CATEGORY_INVALID, code="invalid_request")

    url = f"{base_url or DEFAULT_BASE_URL}/v1/search"

    body: dict[str, Any] = {
        "query": query.strip(),
        "max_results": max_results,
    }

    headers: dict[str, str] = {}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    try:
        _, _, raw = post_json(url, headers, body, timeout)
    except ServiceError as e:
        if e.http_code is None:
            raise  # 网络/超时错误已由 map_http_error 映射好分类
        code = _ERROR_CODES.get(e.http_code, f"http_{e.http_code}")
        raise ServiceError(
            f"AnySearch API error: {e.message}", CATEGORY_HTTP,
            http_code=e.http_code, code=code,
        ) from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"Could not parse API response: {e}", CATEGORY_HTTP, code="api_error") from None

    code = data.get("code", -1)
    if code != 0:
        msg = data.get("message", "Unknown error")
        raise ServiceError(f"API returned error: {msg}", CATEGORY_HTTP, code=f"api_error_{code}")

    return data


# ── MCP 工具调用（extract / batch_search 等，与 REST 并列的官方通道）──────────


def _mcp_call(
    tool_name: str,
    arguments: dict[str, Any],
    api_key: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """调用 AnySearch MCP 工具（JSON-RPC 2.0，POST /mcp）。

    返回 result dict（含 ``content`` 列表，text 项即 Markdown/JSON 字符串）。
    post_json 已把网络/HTTP 错误映射为 ServiceError；JSON-RPC ``error``
    字段在此转为 ServiceError（code 保留服务端语义，如 extract_fetch_failed）。

    Raises:
        ServiceError: 网络 / HTTP / 响应解析失败 / JSON-RPC error。
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    _, _, body = post_json(MCP_ENDPOINT, headers, payload, timeout)
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError as e:
        raise ServiceError(
            f"anysearch MCP: invalid JSON response: {e}", CATEGORY_HTTP
        ) from e
    if not isinstance(data, dict) or data.get("error"):
        err = (data or {}).get("error") or {}
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise ServiceError(f"anysearch MCP error: {msg}", CATEGORY_HTTP)
    return data.get("result") or {}


@register
class AnySearchProvider(Provider):
    """anysearch 后端：web 搜索（匿名可用）+ URL 提取（MCP extract，--use 显式指定）。"""

    name = "anysearch"
    categories = frozenset({"web", "page"})
    # 匿名可用（限流）；配了 key 走正式额度
    config = {
        "api_key": {"secret": True, "hint": "AnySearch API key (optional; works anonymously)"},
        "max_results": {"default": 20, "hint": "number of results (1-20)"},
    }
    priority = {"web": 30}  # page 不声明 priority → 不进默认链（限制多，--use 用）

    def has_credentials(self) -> bool:
        """anysearch 段显式配置了 api_key（None 算未配置；无 key 仍可匿名搜索）。"""
        return self.api_key is not None

    def test_credentials(self) -> str:
        """发最小请求验证凭证/连通性。匿名模式同样视为可用。"""
        t0 = time.monotonic()
        _call_api(
            query="test", api_key=self.api_key, max_results=1,
            timeout=self.timeout(DEFAULT_TIMEOUT),
        )
        elapsed = time.monotonic() - t0
        if self.api_key is None:
            return f"OK (anonymous, {elapsed:.1f}s)"
        return f"OK ({elapsed:.1f}s)"

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        """执行 AnySearch 网页搜索。

        opts 可选键（缺失用 .get 兜底）：
            count(int)  结果数，覆盖 providers.anysearch.max_results
                        （默认 20，API 上限 20，超限 clamp）
        """
        count = opts.get("count")
        if count is None:
            count = self.cfg.get("max_results", DEFAULT_MAX_RESULTS)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = DEFAULT_MAX_RESULTS
        count = max(1, min(count, API_MAX_RESULTS))

        data = _call_api(
            query,
            api_key=self.api_key,
            max_results=count,
            timeout=self.timeout(DEFAULT_TIMEOUT),
        )

        resp_data = data.get("data", {})
        results_raw = resp_data.get("results", [])
        metadata = resp_data.get("metadata", {})

        results = [
            SearchResult(
                title=str(r.get("title", "Untitled")),
                url=str(r.get("url", "")),
                snippet=str(r.get("snippet", "")),
                content=r.get("content"),
            )
            for r in results_raw
        ]

        if not results:
            raise NoResultsError("no results found")

        return SearchResponse(
            query=query.strip(),
            results=results,
            metadata={
                "total_results": metadata.get("total_results", len(results)),
                "search_time_ms": metadata.get("search_time_ms", 0),
                "request_id": data.get("request_id"),
            },
        )

    def fetch(self, url: str, timeout: int = 30) -> FetchResult:
        """URL → Markdown（AnySearch MCP ``extract`` 工具）。

        限制：仅 HTML 页面（PDF/二进制报错）；内容 50,000 字符截断；服务端
        30s 超时。故不进默认 page 链，``--use anysearch`` 显式指定时使用。
        """
        t0 = time.monotonic()
        result = _mcp_call("extract", {"url": url}, self.api_key, timeout)
        md = ""
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                md = str(item.get("text", "")).strip()
                break
        if not md:
            raise ServiceError(
                f"{self.name} returned empty content", CATEGORY_EMPTY
            )
        return FetchResult(
            provider=self.name, content=md, url=url,
            elapsed=round(time.monotonic() - t0, 3),
        )
