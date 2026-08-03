"""OpenAlex 学术论文搜索后端（主后端）。

调用 OpenAlex Works API（GET /works），支持按年份/作者/开放获取过滤，
按引用数/出版日期排序。免凭证，可选配置 mailto 进入礼貌池。纯标准库实现。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
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

DEFAULT_BASE_URL = "https://api.openalex.org"
DEFAULT_MAX_RESULTS = 10
DEFAULT_TIMEOUT = 30.0

# 两阶段排序：sort=cited/date 时先按相关性取候选集（per-page），再在候选内
# 按引用/日期重排截断——避免 API 侧全局重排把只命中个别词的高引无关论文带上来
SORT_CANDIDATE_MULT = 5   # 候选数 = count × 5
SORT_CANDIDATE_MIN = 50
SORT_CANDIDATE_MAX = 200  # OpenAlex per-page 上限


def _candidate_count(count: int) -> int:
    return min(max(count * SORT_CANDIDATE_MULT, SORT_CANDIDATE_MIN), SORT_CANDIDATE_MAX)


def _rank_and_truncate(results: list, sort: str, count: int) -> list:
    """候选集内重排截断：cited 按引用降序、date 按年份降序，缺失键视为 0 排最后。"""
    if sort == "cited":
        results.sort(key=lambda r: (r.extra or {}).get("citations", 0), reverse=True)
    elif sort == "date":
        results.sort(key=lambda r: (r.extra or {}).get("year", 0), reverse=True)
    return results[:count]


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


def _reconstruct_abstract(inv: Any) -> str:
    """abstract_inverted_index（word → [positions]）还原为摘要原文。"""
    if not isinstance(inv, dict):
        return ""
    return " ".join(w for _, w in sorted((p, w) for w, ps in inv.items() for p in ps))


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 OpenAlex 论文搜索。

    opts 可选键（缺失用 .get 兜底）：
        count(int)     结果数，默认 10，clamp 1..50
        timeout(int)   超时秒数，默认 30
        year(str)      出版年份或区间，如 "2023" / "2020-2024"
        author(str)    作者名过滤
        sort(str)      relevance(默认，不传) / cited / date
        oa(bool)       仅开放获取论文
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

    params: dict[str, Any] = {"search": query.strip(), "per-page": count}
    mailto = cfg.get("providers", {}).get("openalex", {}).get("mailto")
    if mailto:
        params["mailto"] = mailto

    filters: list[str] = []
    bounds = _year_bounds(opts)
    if bounds:
        y0, y1 = bounds
        filters.append(f"from_publication_date:{y0}-01-01")
        filters.append(f"to_publication_date:{y1}-12-31")
    author = opts.get("author")
    if author:
        # 实测：authorships.author.display_name.search 不是合法 filter 字段（API 400），
        # 正确的作者名过滤字段是 raw_author_name.search
        filters.append(f"raw_author_name.search:{author}")
    if opts.get("oa"):
        filters.append("open_access.is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)

    sort = opts.get("sort")
    if sort in ("cited", "date"):
        # 两阶段：API 按默认相关性返回候选集，客户端在候选内重排（见 _rank_and_truncate）。
        # 直接传 sort=cited_by_count:desc 会全局重排，多词查询拆词匹配时高引无关论文排最前。
        params["per-page"] = _candidate_count(count)
    # relevance → 不加 sort，API 默认相关性排序

    url = f"{DEFAULT_BASE_URL}/works?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise ServiceError(f"OpenAlex API error ({e.code}): {body}", CATEGORY_HTTP) from None
    except urllib.error.URLError as e:
        raise ServiceError(f"Network error: {e.reason}", CATEGORY_NETWORK) from None
    except TimeoutError:
        raise ServiceError("Request timed out.", CATEGORY_TIMEOUT) from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"Could not parse OpenAlex response: {e}", CATEGORY_HTTP) from None

    meta = data.get("meta") or {}
    try:
        total = int(meta.get("count", 0) or 0)
    except (TypeError, ValueError):
        total = 0

    results: list[SearchResult] = []
    for r in data.get("results", []) or []:
        title = r.get("display_name") or "Untitled"

        authors = [
            a.get("author", {}).get("display_name")
            for a in r.get("authorships", []) or []
        ]
        authors = [str(a) for a in authors if a]

        src = (r.get("primary_location") or {}).get("source")
        venue = src.get("display_name") if isinstance(src, dict) else None

        doi = r.get("doi") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        landing = (r.get("primary_location") or {}).get("landing_page_url")
        if landing:
            page_url = landing
        elif doi:
            page_url = f"https://doi.org/{doi}"
        else:
            page_url = r.get("id") or ""

        extra: dict[str, Any] = {}
        if authors:
            extra["authors"] = authors
        if venue:
            extra["venue"] = venue
        year = r.get("publication_year")
        if isinstance(year, int):
            extra["year"] = year
        citations = r.get("cited_by_count")
        if isinstance(citations, int):
            extra["citations"] = citations
        if doi:
            extra["doi"] = doi
        oa_url = (r.get("open_access") or {}).get("oa_url")
        if oa_url:
            extra["oa_url"] = oa_url

        snippet = _reconstruct_abstract(r.get("abstract_inverted_index"))
        snippet = " ".join(snippet.split())[:300]

        results.append(
            SearchResult(title=str(title), url=page_url, snippet=snippet, extra=extra or None)
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
class OpenAlexProvider(Provider):
    """openalex 学术论文搜索后端（免凭证；年份/作者/OA 过滤 + 引用/日期排序）。"""

    name = "openalex"
    capabilities = frozenset({"search"})
    search_params = {
        "year": ParamSpec(metavar="YEAR", help="[openalex] 出版年份或区间，如 2023 或 2020-2024"),
        "author": ParamSpec(metavar="NAME", help="[openalex] 作者名过滤"),
        "sort": ParamSpec(choices=("relevance", "cited", "date"), help="[openalex] 排序：relevance/cited/date（cited/date 在相关性候选集内重排，避免高引无关论文）"),
        "oa": ParamSpec(action="store_true", help="[openalex] 仅开放获取论文"),
    }

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)
