"""Crossref 论文搜索后端（DOI 元数据兜底）。

调用 Crossref REST API（GET /works），支持作者过滤、年份/开放获取
（近似：has-full-text）过滤与引用数/日期排序。免凭证。纯标准库实现。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..base import Provider, SearchResponse, SearchResult
from ..errors import (
    CATEGORY_HTTP,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    NoResultsError,
    ServiceError,
)
from ..registry import register

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.crossref.org"
DEFAULT_MAX_RESULTS = 10
DEFAULT_TIMEOUT = 30.0

# 两阶段排序（同 openalex）：先按相关性取候选集，再在候选内按引用/日期重排
SORT_CANDIDATE_MULT = 5
SORT_CANDIDATE_MIN = 50
SORT_CANDIDATE_MAX = 200  # Crossref rows 上限 1000，200 以内礼貌且够用

# Crossref 常见垃圾条目：图的子条目（"Figure 6: ..."）不是论文
_FIG_TITLE_RE = re.compile(r"^(Figure|Table|Fig\.?)\s*\d+", re.IGNORECASE)


def _candidate_count(count: int) -> int:
    return min(max(count * SORT_CANDIDATE_MULT, SORT_CANDIDATE_MIN), SORT_CANDIDATE_MAX)


def _rank_and_truncate(results: list, sort: str, count: int) -> list:
    """候选集内重排截断：cited 按引用降序、date 按年份降序，缺失键视为 0 排最后。"""
    if sort == "cited":
        results.sort(key=lambda r: (r.extra or {}).get("citations", 0), reverse=True)
    elif sort == "date":
        results.sort(key=lambda r: (r.extra or {}).get("year", 0), reverse=True)
    return results[:count]

# 摘要字段是 JATS XML 字符串，剥掉全部标签
_TAG_RE = re.compile(r"<[^>]+>")


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


def _pub_year(item: dict) -> int | None:
    """出版年份：issued → published-online → published-print，取不到返回 None。"""
    for key in ("issued", "published-online", "published-print"):
        parts = (item.get(key) or {}).get("date-parts")
        # 注意：真实 API 存在 date-parts=[[]]（未知日期）的空内层，需跳过
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
            if isinstance(year, int):
                return year
    return None


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 Crossref 论文搜索。

    opts 可选键（缺失用 .get 兜底）：
        count(int)     结果数，默认 10，clamp 1..50
        timeout(int)   超时秒数，默认 30
        year(str)      出版年份或区间，如 "2023" / "2020-2024"
        author(str)    作者名过滤
        sort(str)      relevance(默认) / cited / date
        oa(bool)       仅开放获取（近似：has-full-text:true）
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

    params: dict[str, Any] = {"query": query.strip(), "rows": count}

    author = opts.get("author")
    if author:
        params["query.author"] = author

    filters: list[str] = []
    bounds = _year_bounds(opts)
    if bounds:
        y0, y1 = bounds
        filters.append(f"from-pub-date:{y0}-01-01")
        filters.append(f"until-pub-date:{y1}-12-31")
    if opts.get("oa"):
        # 近似：Crossref 无直接 OA 标志，has-full-text 表示存在全文链接
        filters.append("has-full-text:true")
    if filters:
        params["filter"] = ",".join(filters)

    sort = opts.get("sort")
    if sort in ("cited", "date"):
        # 两阶段：先按 API 默认相关性取候选集，客户端在候选内重排截断。
        # 直接 sort=is-referenced-by-count 会全局重排，多词查询时高引无关论文排最前。
        params["rows"] = _candidate_count(count)
    # relevance → 不加，API 默认相关性排序

    url = f"{DEFAULT_BASE_URL}/works?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise ServiceError(f"Crossref API error ({e.code}): {body}", CATEGORY_HTTP) from None
    except urllib.error.URLError as e:
        raise ServiceError(f"Network error: {e.reason}", CATEGORY_NETWORK) from None
    except TimeoutError:
        raise ServiceError("Request timed out.", CATEGORY_TIMEOUT) from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"Could not parse Crossref response: {e}", CATEGORY_HTTP) from None

    message = data.get("message") or {}
    try:
        total = int(message.get("total-results", 0) or 0)
    except (TypeError, ValueError):
        total = 0

    results: list[SearchResult] = []
    for item in message.get("items", []) or []:
        title = (item.get("title") or [""])[0]
        if not title:
            title = "Untitled"

        if _FIG_TITLE_RE.match(title):
            continue  # 图/表子条目不是论文，丢弃

        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in item.get("author", []) or []
        ]
        authors = [a for a in authors if a]

        venue = (item.get("container-title") or [""])[0]
        year = _pub_year(item)
        citations = item.get("is-referenced-by-count", 0)
        doi = item.get("DOI") or ""

        oa_url = ""
        for link in item.get("link", []) or []:
            ct = link.get("content-type") or ""
            if ct.startswith("text/html"):
                oa_url = link.get("URL") or ""
                break

        extra: dict[str, Any] = {}
        if authors:
            extra["authors"] = authors
        if venue:
            extra["venue"] = venue
        if year is not None:
            extra["year"] = year
        if isinstance(citations, int):
            extra["citations"] = citations
        if doi:
            extra["doi"] = doi
        if oa_url:
            extra["oa_url"] = oa_url

        abstract = item.get("abstract") or ""
        snippet = _TAG_RE.sub(" ", abstract)
        snippet = " ".join(snippet.split())[:300]

        results.append(
            SearchResult(
                title=str(title),
                url=f"https://doi.org/{doi}" if doi else "",
                snippet=snippet,
                extra=extra or None,
            )
        )

    if not results:
        raise NoResultsError("未找到结果")

    if sort in ("cited", "date"):
        results = _rank_and_truncate(results, sort, count)

    return SearchResponse(
        query=query.strip(),
        results=results,
        metadata={"total_results": total},
    )


@register
class CrossrefProvider(Provider):
    """crossref 论文搜索后端（免凭证；作者/年份/OA 过滤 + 引用/日期排序）。"""

    name = "crossref"
    categories = frozenset({"search.paper"})

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)
