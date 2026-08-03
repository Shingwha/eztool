"""公共入口：search_category（按类别路由搜索）/ convert（按输入类型路由转换）。

统一配置（``providers.<name>`` 段 + ``search.<类别>`` / ``convert.<类别>`` 段）
→ ProviderOpts → 统一回退链 / fan-out。类别是路由单元：回退链候选来自
registry.CATEGORIES，命令参数按类别归属——参数与后端错配从模型上消失。

- search.paper：三源并行 fan-out（openalex+arxiv+crossref，合并去重）；
- 其余 search.* 类别：回退链（第一个成功即返回，失败自动换下一个）；
- convert：http(s):// → convert.page 链；本地路径 → convert.file 链。
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from . import config as cfgmod
from .base import FetchResult, ProviderOpts, SearchResponse, SearchResult
from .chain import run_chain, run_fanout
from .errors import (
    CATEGORY_ALL_FAILED,
    ServiceError,
    UsageError,
)
from .registry import (
    SERVICES,
    providers_for,
    service_names,
)

# 确保所有服务商注册（side-effect: @register）
from . import providers as _providers  # noqa: F401

__all__ = [
    "search_category", "convert",
    "list_providers", "list_category_providers",
]

# provider 段缺省超时（未配置 providers.<name>.timeout 时回退）
DEFAULT_TIMEOUTS = {
    "firecrawl": 60, "markdown_new": 30, "jina_reader": 10, "mineru": 300,
}


# ── 配置 → ProviderOpts ────────────────────────────────────────────────────


def _provider_opts(cfg: dict, section: dict) -> ProviderOpts:
    """从 providers.<name> 段构建 ProviderOpts（timeouts + api_keys）。

    timeout：provider 子段 > 类别段缺省（search.<cat>.timeout 等）> DEFAULT_TIMEOUTS。
    """
    timeouts: dict = {}
    api_keys: dict = {}
    providers_cfg = cfg.get("providers") or {}
    if not isinstance(providers_cfg, dict):
        providers_cfg = {}
    sec_default = section.get("timeout", 30)
    for name in service_names():
        sec = providers_cfg.get(name)
        if not isinstance(sec, dict):
            sec = {}
        to = sec.get("timeout")
        if isinstance(to, int) and to > 0:
            timeouts[name] = to
        else:
            timeouts[name] = DEFAULT_TIMEOUTS.get(name, sec_default)
        key = sec.get("api_key")
        if key:
            api_keys[name] = key
    return ProviderOpts(timeouts=timeouts, api_keys=api_keys)


def _section(cfg: dict, category: str) -> dict:
    """取 ``<域>.<操作>`` 对应的配置段（search.web / convert.page …）。"""
    domain, _, op = category.partition(".")
    sec = cfg.get(domain) or {}
    if not isinstance(sec, dict):
        sec = {}
    sub = sec.get(op)
    if not isinstance(sub, dict):
        sub = {}
    return sub


def _chain_providers(section: dict, opts: dict, defaults: list[str]) -> list[str]:
    """回退链：opts.providers（逗号分隔）覆盖配置，再回退类别注册顺序。"""
    raw = opts.get("providers")
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    providers = section.get("providers")
    if not isinstance(providers, list) or not providers:
        return list(defaults)
    return providers


def _log_collector(reasons: list[str]):
    def log(msg: str) -> None:
        print(msg, file=sys.stderr)  # stderr 日志原样保留
        reasons.append(msg)
    return log


def _check_provider_names(names: list[str]) -> None:
    """未知 provider 名是用法错误（硬停），而不是静默 all-failed。"""
    known = service_names()
    for n in names:
        if n not in known:
            raise UsageError(
                f"未知 provider '{n}'（可用: {', '.join(known)}）"
            )


def _param_owners(category: str) -> dict[str, str]:
    """该类别 provider 特有参数 → 归属 provider（参数归属校验用）。"""
    owners: dict[str, str] = {}
    for name in providers_for(category):
        for pname in (SERVICES[name].category_params or {}).get(category, {}):
            owners[pname] = name
    return owners


def _check_params(names: list[str], category: str, opts: dict) -> None:
    """传了不属于候选 provider 的特有参数 → UsageError（exit 2）。"""
    owners = _param_owners(category)
    label = ",".join(names) if len(names) > 1 else names[0]
    for pname, val in opts.items():
        if not val:
            continue
        owner = owners.get(pname)
        if owner and owner not in names:
            raise UsageError(
                f"参数 --{pname.replace('_', '-')} 仅支持 {owner} provider（当前: {label}）"
            )


# ── search ─────────────────────────────────────────────────────────────────


def search_category(
    cfg: dict, category: str, query: str, opts: dict | None = None,
) -> SearchResponse:
    """按类别路由搜索。category 决定回退链与参数面（providers_for 校验类别）。"""
    providers_for(category)  # 未知类别 → ServiceError(CATEGORY_INVALID)
    opts = dict(opts or {})
    if category == "search.paper":
        return _paper(cfg, query, opts)

    section = _section(cfg, category)
    popts = _provider_opts(cfg, section)
    names = _chain_providers(section, opts, providers_for(category))
    _check_provider_names(names)
    _check_params(names, category, opts)
    if category == "search.image":
        opts["image"] = True  # doubao 图片模式开关（provider 方法零改动）

    reasons: list[str] = []
    result = run_chain(
        names, category,
        lambda svc: svc.search(cfg, query, opts),
        popts, log=_log_collector(reasons),
    )
    if result is None:
        detail = "; ".join(reasons) or f"providers={names}"
        raise ServiceError(
            f"all providers failed: {detail}", CATEGORY_ALL_FAILED,
            code="search_failed",
        )
    resp, name = result
    if resp.metadata is None:
        resp.metadata = {}
    resp.metadata.setdefault("backend", name)
    return resp


def _paper(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """论文搜索：默认并发搜 openalex + arxiv + crossref，合并去重。

    opts 键：providers（逗号分隔，覆盖 search.paper.providers 配置）、count、
    timeout、year、author、sort（relevance/cited/date）、oa、full。
    """
    section = _section(cfg, "search.paper")
    popts = _provider_opts(cfg, section)
    names = _chain_providers(section, opts, providers_for("search.paper"))
    _check_provider_names(names)
    return _search_fanout(cfg, names, query, opts, popts)


def _search_fanout(
    cfg: dict, names: list[str], query: str, opts: dict, popts: ProviderOpts,
) -> SearchResponse:
    """fan-out 搜索：并行搜全部 names，合并去重；全部失败抛错。"""
    reasons: list[str] = []
    paired = run_fanout(
        names, "search.paper",
        lambda svc: svc.search(cfg, query, opts),
        popts, log=_log_collector(reasons),
    )
    if not paired:
        detail = "; ".join(reasons) or f"providers={names}"
        raise ServiceError(
            f"all providers failed: {detail}", CATEGORY_ALL_FAILED,
            code="search_failed",
        )
    return _merge_search(paired, sort=opts.get("sort"))


def _merge_search(paired: list[tuple[str, SearchResponse]], sort: str | None = None) -> SearchResponse:
    """多源结果合并去重（fan-out 汇总）。

    paired 已按 provider 顺序排列（来自 run_fanout）。去重 key 优先级：
    ``extra["doi"]``（小写）→ ``url``（去尾斜杠、小写）→ 归一化 title
    （小写、压缩空白）。有 key 才去重，first wins（先到的保留）。
    保留的结果回填 ``source``。排序：None=保持 provider 分组顺序；
    "cited"=按 citations 降序；"date"=按 year 降序。
    """
    seen: set = set()
    merged: list[SearchResult] = []
    per_provider: dict[str, int] = {}
    query = ""
    for name, resp in paired:
        per_provider[name] = len(resp.results)
        if not query:
            query = resp.query
        for r in resp.results:
            key = None
            extra = r.extra or {}
            doi = extra.get("doi")
            if doi:
                key = ("doi", str(doi).lower())
            elif r.url:
                key = ("url", r.url.rstrip("/").lower())
            elif r.title:
                key = ("title", " ".join(r.title.lower().split()))
            if key is None:
                merged.append(r)
            elif key not in seen:
                seen.add(key)
                merged.append(r)
            else:
                continue  # 重复，丢弃（first wins）
            r.source = name

    if sort == "cited":
        merged.sort(key=lambda r: (r.extra or {}).get("citations", 0), reverse=True)
    elif sort == "date":
        merged.sort(key=lambda r: (r.extra or {}).get("year", 0), reverse=True)

    names = [name for name, _ in paired]
    return SearchResponse(
        query=query,
        results=merged,
        metadata={
            "backend": ",".join(names),
            "total_results": len(merged),
            "per_provider": per_provider,
            "search_time_ms": 0,
        },
    )


# ── convert ────────────────────────────────────────────────────────────────


def convert(cfg: dict, target: str, opts: dict | None = None) -> FetchResult:
    """按输入类型路由转换：URL → convert.page 链；本地路径 → convert.file 链。

    opts 只取 timeout（int|None）与 providers（逗号分隔字符串，覆盖配置链）。
    本地路径不存在 → UsageError（提示路径错误）。全部 provider 失败抛
    ServiceError(code="convert_failed")。
    """
    opts = opts or {}
    if urlparse(target).scheme in ("http", "https"):
        category = "convert.page"
        invoke = lambda svc: svc.fetch(target, timeout=svc.timeout(30))  # noqa: E731
    else:
        if not os.path.exists(target):
            raise UsageError(f"文件不存在: {target}")
        category = "convert.file"
        invoke = lambda svc: svc.convert_file(target, timeout=svc.timeout(60))  # noqa: E731

    section = _section(cfg, category)
    popts = _provider_opts(cfg, section)
    names = _chain_providers(section, opts, providers_for(category))
    _check_provider_names(names)

    reasons: list[str] = []
    result = run_chain(
        names, category, invoke, popts, log=_log_collector(reasons),
    )
    if result is None:
        detail = "; ".join(reasons) or f"providers={names}"
        raise ServiceError(
            f"all providers failed: {detail}", CATEGORY_ALL_FAILED,
            code="convert_failed",
        )
    return result[0]


# ── 列表 ───────────────────────────────────────────────────────────────────


def list_providers() -> list[str]:
    return service_names()


def list_category_providers(category: str) -> list[str]:
    """指定类别（convert.page / convert.file）的 provider 候选。"""
    return providers_for(category)
