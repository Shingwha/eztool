"""AnySearch 后端（搜索 + URL 提取）。

- 搜索：AnySearch REST API（POST /v1/search），支持匿名（无 API Key）与
  Bearer Token 鉴权，支持 tag 参数定向数据源。
- URL 提取（convert.page）：MCP JSON-RPC 通道（POST /mcp 的 tools/call，
  ``extract`` 工具），匿名可用；限制：仅 HTML（PDF/二进制报错）、内容
  50,000 字符截断、服务端 30s 超时——故放在 convert.page 链中间位，
  firecrawl 兜底全文。

纯标准库实现。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..base import FetchResult, ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    NoResultsError,
    ServiceError,
    UsageError,
)
from ..http import http_post
from ..registry import register

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.anysearch.com"
MCP_ENDPOINT = "https://api.anysearch.com/mcp"  # MCP JSON-RPC 通道（extract 等工具）
DEFAULT_MAX_RESULTS = 10
API_MAX_RESULTS = 20  # API 上限
DEFAULT_TIMEOUT = 60.0

# ── 数据源标签（来自官方文档，保留原顺序）─────────────────────────────────────


def _tags() -> list[tuple[str, str]]:
    # 原仓库是 dict[name]=desc；转为 list[tuple] 供 `eztool tags` 输出
    return [
        # General
        ("general.general", "General web search"),
        # Academic
        ("academic.search", "Cross-disciplinary paper search (keywords/title/author/institution)"),
        ("academic.biomedical", "Biomedical literature search (MEDLINE/MeSH/PMC full text)"),
        ("academic.citation", "Citation graph and citation count search"),
        ("academic.dataset", "Research datasets and open-source code (Zenodo/Dryad/Figshare)"),
        ("academic.preprint", "Preprint search (CS/physics/math/biology/economics)"),
        # Agriculture
        ("agriculture.fao", "FAO global agriculture stats (yield/trade/food prices)"),
        # Business
        ("business.company", "Company registration/shareholders/executives/business status"),
        ("business.jobs", "Global job search (by skill/location/salary)"),
        ("business.people", "Business contact search (title/company/location)"),
        ("business.trade", "International trade stats (HS code/country/time)"),
        # Code
        ("code.doc", "Developer docs and code examples (npm/PyPI/Cargo)"),
        ("code.snippet", "GitHub public repo code search (regex/language filter)"),
        # Energy
        ("energy.electricity", "Electricity market data (price/generation/demand/carbon intensity)"),
        ("energy.production", "Energy production and consumption stats (oil/gas/coal/nuclear/renewable)"),
        # Environment
        ("environment.aqi", "Global real-time air quality index (AQI/PM2.5/PM10)"),
        # Film
        ("film.torrent", "Movie/music BT resource search (magnet link/size/seeders)"),
        # Finance
        ("finance.quote", "Real-time and historical quotes (stocks/forex/crypto/commodities/indices)"),
        ("finance.news", "Global financial news and company announcements"),
        ("finance.fundamental", "Financial statements/valuation/analyst ratings/SEC filings"),
        ("finance.calendar", "Earnings/economic data/IPO calendar"),
        ("finance.macro", "Macroeconomic indicators (GDP/CPI/PMI/interest rates/money supply)"),
        ("finance.screen", "Stock screener (market cap/PE/dividend yield/sector/country)"),
        # Gaming
        ("gaming.esports", "Esports player stats/rankings/hero attributes (LoL etc.)"),
        ("gaming.store", "Steam real-time prices/discounts/ratings/concurrent players"),
        # Health
        ("health.drug", "Drug labels/adverse reactions/interactions/recalls"),
        ("health.stats", "Global public health stats (194 countries: mortality/morbidity/life expectancy)"),
        ("health.trial", "Clinical trial registry search (condition/drug/phase/region)"),
        # IP
        ("ip.global", "Global patent search and family tracking (EPO DOCDB/INPADOC)"),
        # Legal
        ("legal.case", "Court rulings and legal opinions (CN/US/CA/ECHR)"),
        ("legal.legislation", "Legislative tracking (US Congress bills/votes/deliberations)"),
        ("legal.statute", "Statute and regulation search (with section anchors and version history)"),
        # Resources
        ("resource.image", "Professional photography/stock/SVG/illustrations/vectors"),
        # Security
        ("security.intel", "Threat intelligence (IP/domain/URL/file hash/IOC)"),
        ("security.noise", "IPv4 background scan noise detection"),
        ("security.scan", "File hash/URL/IP/domain multi-vendor scan aggregation"),
        ("security.vuln", "CVE details (CVSS score/affected versions/patch links)"),
        # Social media
        ("social_media.social_media", "Social media information search and retrieval"),
        # Travel
        ("travel.flight", "Global flight search (origin/destination/date/cabin/luggage/compare)"),
        ("travel.flight_status", "Real-time flight status (departure/arrival/gate/delay)"),
    ]


KNOWN_TAGS: list[tuple[str, str]] = _tags()

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


# ── 凭证 ──────────────────────────────────────────────────────────────────────


def _api_key(cfg: dict) -> str | None:
    return cfg.get("providers", {}).get("anysearch", {}).get("api_key")


def _has_credentials(cfg: dict) -> bool:
    """anysearch 段显式配置了 api_key（None 也算未配置；无 key 仍可匿名搜索）。"""
    return _api_key(cfg) is not None


def _test_credentials(cfg: dict) -> str:
    """发最小请求验证凭证/连通性。匿名模式同样视为可用。"""
    api_key = _api_key(cfg)
    t0 = time.monotonic()
    _call_api(query="test", api_key=api_key, max_results=1, timeout=DEFAULT_TIMEOUT)
    elapsed = time.monotonic() - t0
    if api_key is None:
        return f"OK (anonymous, {elapsed:.1f}s)"
    return f"OK ({elapsed:.1f}s)"


# ── 核心 API 调用（原 core.py 逻辑，SearchError → ServiceError）──────────────


def _call_api(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    tag: str | None = None,
    zone: str | None = None,
    language: str | None = None,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
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
    if tag:
        body["tag"] = tag
    if zone:
        body["zone"] = zone
    if language:
        body["language"] = language
    if params:
        body["params"] = params

    payload = json.dumps(body).encode("utf-8")
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        err_text: str
        try:
            err_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_text = "Unable to read error body"
        err_data: dict[str, Any] = {}
        try:
            err_data = json.loads(err_text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        error_msg = err_data.get("message", err_text)
        code = _ERROR_CODES.get(e.code, f"http_{e.code}")
        raise ServiceError(
            f"AnySearch API error ({e.code}): {error_msg}", CATEGORY_HTTP, code=code
        ) from None
    except urllib.error.URLError as e:
        raise ServiceError(f"Network error: {e.reason}", CATEGORY_NETWORK, code="network_error") from None
    except TimeoutError:
        raise ServiceError("Request timed out.", CATEGORY_TIMEOUT, code="network_error") from None

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
    timeout: float = 30.0,
) -> dict[str, Any]:
    """调用 AnySearch MCP 工具（JSON-RPC 2.0，POST /mcp）。

    返回 result dict（含 ``content`` 列表，text 项即 Markdown/JSON 字符串）。
    http_post 已把网络/HTTP 错误映射为 ServiceError；JSON-RPC ``error``
    字段在此转为 ServiceError（code 保留服务端语义，如 extract_fetch_failed）。

    Raises:
        ServiceError: 网络 / HTTP / 响应解析失败 / JSON-RPC error。
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    _, _, body = http_post(MCP_ENDPOINT, headers, payload, int(timeout))
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


# ── 公开搜索入口 ──────────────────────────────────────────────────────────────


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 AnySearch 搜索。

    opts 可选键（缺失用 .get 兜底）：
        count(int)     结果数，覆盖 cfg.anysearch.max_results（默认 10，API 上限 20，超限 clamp）
        timeout(int)   超时秒数，覆盖默认 60
        tag(str)       数据源标签（见 KNOWN_TAGS）
        zone(str)      区域（"cn"/"intl"）
        language(str)  语言（如 "zh-CN"/"en"）
        params(str)    额外参数 JSON 字符串（如 {"ticker": "AAPL"}）
        anonymous(bool) True 强制匿名（忽略配置的 api_key）
        full(bool)     保留字段，结果 content 有则始终填充
    """
    if opts.get("anonymous"):
        api_key = None
    else:
        api_key = _api_key(cfg)

    count = opts.get("count")
    if count is None:
        count = cfg.get("providers", {}).get("anysearch", {}).get("max_results", DEFAULT_MAX_RESULTS)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_MAX_RESULTS
    count = max(1, min(count, API_MAX_RESULTS))

    try:
        timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    tag = opts.get("tag")
    zone = opts.get("zone")
    language = opts.get("language")

    params: dict[str, Any] | None = None
    raw_params = opts.get("params")
    if raw_params is not None:
        try:
            params = json.loads(raw_params)
        except (json.JSONDecodeError, TypeError) as e:
            raise UsageError(
                f"--params 必须是 JSON 对象字符串: {e}"
            ) from None
        if not isinstance(params, dict):
            raise UsageError("--params 必须是 JSON 对象（如 {\"ticker\": \"AAPL\"}）")

    data = _call_api(
        query,
        api_key=api_key,
        max_results=count,
        tag=tag,
        zone=zone,
        language=language,
        params=params,
        timeout=timeout,
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
        raise NoResultsError("未找到结果")

    return SearchResponse(
        query=query.strip(),
        results=results,
        metadata={
            "total_results": metadata.get("total_results", len(results)),
            "search_time_ms": metadata.get("search_time_ms", 0),
            "request_id": data.get("request_id"),
        },
    )


@register
class AnySearchProvider(Provider):
    """anysearch 后端：搜索（匿名可用；tag 定向数据源）+ URL 提取（MCP extract）。"""

    name = "anysearch"
    categories = frozenset({"search.web", "search.data", "convert.page"})
    category_params = {
        "search.data": {
            "tag": ParamSpec(metavar="TAG", help="数据源标签（见 eztool search tags）"),
            "params": ParamSpec(
                metavar="JSON",
                help="额外参数 JSON，如 {\"type\": \"quote\"}（部分标签必填，见 search tags 说明）",
            ),
        },
    }

    def has_credentials(self, cfg: dict) -> bool:
        return _has_credentials(cfg)

    def test_credentials(self, cfg: dict) -> str:
        return _test_credentials(cfg)

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)

    def build_headers(self) -> dict:
        """MCP 通道头：JSON-RPC 需 Content-Type；api_key 可选（匿名可用）。"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def fetch(self, url: str, timeout: int = 30) -> FetchResult:
        """URL → Markdown（AnySearch MCP ``extract`` 工具）。

        限制：仅 HTML 页面（PDF/二进制报错）；内容 50,000 字符截断；服务端
        30s 超时。故只作 convert.page 链的中间位，firecrawl 兜底全文。
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
