"""arXiv 预印本搜索后端（兜底）。

调用 arXiv API（GET /api/query，Atom XML），支持作者过滤、年份后过滤
（API 无年份参数）与提交日期排序。预印本全部开放获取，oa 无操作。
免凭证。纯标准库实现。
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from ..base import ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import (
    CATEGORY_HTTP,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    NoResultsError,
    ServiceError,
)
from ..registry import register

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://export.arxiv.org"
DEFAULT_MAX_RESULTS = 10
DEFAULT_TIMEOUT = 30.0

# Atom 响应命名空间
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


def _year_bounds(opts: dict) -> tuple[int, int] | None:
    """解析 year：`"2023"` → (2023, 2023)；`"2020-2024"` → (2020, 2024)；非法格式静默忽略。"""
    raw = opts.get("year")
    if not raw:
        return None
    parts = str(raw).split("-")
    try:
        if len(parts) == 1:
            y = int(parts[0])
            return (y, y)
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
    except ValueError:
        pass
    return None


def _quote_if_space(s: str) -> str:
    """多词短语用引号包起来（arXiv search_query 语法要求）。"""
    return f'"{s}"' if " " in s else s


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 arXiv 预印本搜索。

    opts 可选键（缺失用 .get 兜底）：
        count(int)     结果数，默认 10，clamp 1..50
        timeout(int)   超时秒数，默认 30
        year(str)      出版年份或区间（API 无年份过滤，解析后 post-filter）
        author(str)    作者名过滤
        sort(str)      relevance(默认) / date（按提交日期）；cited 不支持，按默认
        oa(bool)       无操作（预印本全部开放获取）
    """
    try:
        count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
    except (TypeError, ValueError):
        count = DEFAULT_MAX_RESULTS
    count = max(1, min(count, 50))

    try:
        timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    q = query.strip()
    author = opts.get("author")
    if author:
        search_query = f"au:{_quote_if_space(str(author).strip())} AND all:{_quote_if_space(q)}"
    else:
        search_query = f"all:{_quote_if_space(q)}"

    params: dict[str, Any] = {
        "search_query": search_query,
        "max_results": count,
        "start": 0,
        "sortBy": "submittedDate" if opts.get("sort") == "date" else "relevance",
    }
    if opts.get("sort") == "date":
        params["sortOrder"] = "descending"

    url = f"{DEFAULT_BASE_URL}/api/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise ServiceError(f"arXiv API error ({e.code}): {body}", CATEGORY_HTTP) from None
    except urllib.error.URLError as e:
        raise ServiceError(f"Network error: {e.reason}", CATEGORY_NETWORK) from None
    except TimeoutError:
        raise ServiceError("Request timed out.", CATEGORY_TIMEOUT) from None

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ServiceError(f"Could not parse arXiv response: {e}", CATEGORY_HTTP) from None

    bounds = _year_bounds(opts)

    results: list[SearchResult] = []
    for entry in root.findall("a:entry", NS):
        # 年份：a:published 形如 "2023-06-01T17:03:04Z"，取前 4 位
        year: int | None = None
        pub = entry.findtext("a:published", default="", namespaces=NS) or ""
        if len(pub) >= 4 and pub[:4].isdigit():
            year = int(pub[:4])
        # 年份后过滤（API 无年份参数）
        if bounds and (year is None or not (bounds[0] <= year <= bounds[1])):
            continue

        title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip()
        page_url = (entry.findtext("a:id", default="", namespaces=NS) or "").strip()
        authors = [n.text for n in entry.findall("a:author/a:name", NS) if n.text]
        venue = (entry.findtext("a:journal_ref", default="", namespaces=NS) or "").strip()
        doi = (entry.findtext("arxiv:doi", default="", namespaces=NS) or "").strip()
        summary = entry.findtext("a:summary", default="", namespaces=NS) or ""

        extra: dict[str, Any] = {}
        if authors:
            extra["authors"] = authors
        if venue:
            extra["venue"] = venue
        if year is not None:
            extra["year"] = year
        if doi:
            extra["doi"] = doi
        # arXiv 无引用数，citations 不放

        snippet = " ".join(summary.split())[:300]

        results.append(
            SearchResult(title=title, url=page_url, snippet=snippet, extra=extra or None)
        )

    if not results:
        raise NoResultsError("未找到结果")

    total = len(results)
    tr = root.findtext("opensearch:totalResults", default="", namespaces=NS) or ""
    if tr.isdigit():
        total = int(tr)

    return SearchResponse(
        query=query.strip(),
        results=results,
        metadata={"total_results": total},
    )


@register
class ArxivProvider(Provider):
    """arxiv 预印本搜索后端（免凭证；支持作者/年份/日期排序）。"""

    name = "arxiv"
    capabilities = frozenset({"search"})

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)
