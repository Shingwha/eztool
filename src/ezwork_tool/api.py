"""公共入口：search / fetch / convert / paper + 服务商列表。

统一配置（``providers.<name>`` 段）→ ProviderOpts → 统一回退链 / fan-out。
search 的 ``auto`` 模式是真正的 failover：doubao 失败自动试 deepseek →
anysearch；显式 ``--backend`` 只试该后端；逗号分隔的 ``--backend``
（如 ``openalex,arxiv``）是多后端并行汇总（fan-out：同时搜、合并去重）。
paper 默认并发搜 openalex + arxiv + crossref 三个论文源并合并去重。
"""

from __future__ import annotations

import sys

from . import config as cfgmod
from .base import FetchResult, ProviderOpts, SearchResponse, SearchResult
from .chain import convert_chain, fetch_chain, run_chain, run_fanout
from .errors import (
    CATEGORY_ALL_FAILED,
    CATEGORY_HTTP,
    ServiceError,
    UsageError,
)
from .registry import (
    file_convert_services,
    search_param_owners,
    search_services,
    service_names,
)

# 确保所有服务商注册（side-effect: @register）
from . import providers as _providers  # noqa: F401

__all__ = [
    "search", "fetch", "convert", "paper",
    "list_providers", "list_convert_providers", "search_backend_names",
]

# auto 模式的 failover 顺序（免费/匿名可用优先，可被配置 search.providers 覆盖；
# 与 config.DEFAULTS 同源，测试 monkeypatch 此名称即可）
AUTO_SEARCH_ORDER = cfgmod.DEFAULTS["search"]["providers"]

# paper 默认三源顺序（与 config.DEFAULTS 同源，测试可 monkeypatch）
PAPER_ORDER = cfgmod.DEFAULTS["paper"]["providers"]

# provider 段缺省超时（与 config.DEFAULTS 一致）；未配置段的 provider 回退段超时
DEFAULT_TIMEOUTS = {"firecrawl": 60, "markdown": 30, "jina": 10, "mineru": 300}


# ── 配置 → ProviderOpts ────────────────────────────────────────────────────


def _provider_opts(cfg: dict, section: dict) -> ProviderOpts:
    """从 providers.<name> 段构建 ProviderOpts（timeouts + api_keys）。

    timeout：provider 子段 > 段缺省（fetch/convert.timeout）> DEFAULT_TIMEOUTS。
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


def _chain_providers(section: dict, opts: dict, defaults: list[str]) -> list[str]:
    """回退链：opts.providers（逗号分隔）覆盖配置，再回退默认。"""
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


# ── search ─────────────────────────────────────────────────────────────────


def _check_params(names: list[str], opts: dict) -> None:
    """传了不属于候选后端的特有参数 → UsageError（exit 2）。"""
    owners = search_param_owners()
    label = ",".join(names) if len(names) > 1 else names[0]
    for pname, val in opts.items():
        if not val:
            continue
        owner = owners.get(pname)
        if owner and owner not in names:
            raise UsageError(
                f"参数 --{pname.replace('_', '-')} 仅支持 {owner} 后端（当前: {label}）"
            )


def search(
    cfg: dict,
    query: str,
    backend: str = "auto",
    opts: dict | None = None,
) -> SearchResponse:
    """搜索。backend=auto → AUTO_SEARCH_ORDER 逐个 failover；显式 → 单后端；
    逗号分隔（如 ``openalex,arxiv``）→ 多后端并行汇总（fan-out + 合并去重）。"""
    opts = opts or {}
    if backend == "auto":
        # 顺序可配置：search.providers（配置）> opts.providers（CLI）> 默认
        search_cfg = cfg.get("search") or {}
        if not isinstance(search_cfg, dict):
            search_cfg = {}
        names = _chain_providers(search_cfg, opts, AUTO_SEARCH_ORDER)
    elif "," in backend:
        names = [b.strip() for b in backend.split(",") if b.strip()]
        if not names:
            raise UsageError(f"未知后端 '{backend}'（可用: auto, {', '.join(search_services())}）")
        for b in names:
            if b not in search_services():
                known = ", ".join(search_services())
                raise UsageError(f"未知后端 '{b}'（可用: auto, {known}）")
    else:
        names = [backend]
        if backend not in search_services():
            known = ", ".join(search_services())
            raise UsageError(f"未知后端 '{backend}'（可用: auto, {known}）")
    _check_params(names, opts)

    if "," in backend:
        resp = _search_fanout(cfg, names, query, opts)
        resp.metadata = resp.metadata or {}
        resp.metadata["backend"] = ",".join(names)
        return resp

    reasons: list[str] = []
    result = run_chain(
        names, "search",
        lambda svc: svc.search(cfg, query, opts),
        log=_log_collector(reasons),
    )
    if result is None:
        detail = "; ".join(reasons) or f"backends={names}"
        raise ServiceError(
            f"all backends failed: {detail}", CATEGORY_ALL_FAILED,
            code="search_failed",
        )
    resp, name = result
    if resp.metadata is None:
        resp.metadata = {}
    resp.metadata.setdefault("backend", name)
    return resp


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


def _search_fanout(cfg: dict, names: list[str], query: str, opts: dict) -> SearchResponse:
    """fan-out 搜索入口：并行搜全部 names，合并去重；全部失败抛错。"""
    reasons: list[str] = []
    paired = run_fanout(
        names, "search",
        lambda svc: svc.search(cfg, query, opts),
        log=_log_collector(reasons),
    )
    if not paired:
        detail = "; ".join(reasons) or f"backends={names}"
        raise ServiceError(
            f"all backends failed: {detail}", CATEGORY_ALL_FAILED,
            code="search_failed",
        )
    return _merge_search(paired, sort=opts.get("sort"))


def paper(cfg: dict, query: str, opts: dict | None = None) -> SearchResponse:
    """论文搜索：默认并发搜 openalex + arxiv + crossref，合并去重。

    opts 键：providers（逗号字符串覆盖默认三源）、count、timeout、year、
    author、sort（relevance/cited/date）、oa、full（透传 formatter 用）。
    """
    opts = opts or {}
    paper_cfg = cfg.get("paper") or {}
    if not isinstance(paper_cfg, dict):
        paper_cfg = {}
    names = _chain_providers(paper_cfg, opts, PAPER_ORDER)
    for b in names:
        if b not in search_services():
            known = ", ".join(search_services())
            raise UsageError(f"未知后端 '{b}'（可用: {known}）")
    return _search_fanout(cfg, names, query, opts)


# ── fetch / convert ────────────────────────────────────────────────────────


def fetch(cfg: dict, url: str, opts: dict | None = None) -> FetchResult:
    """按回退链抓取 URL 转 Markdown。

    opts 只取 timeout（int|None）与 providers（逗号分隔字符串，覆盖配置链）。
    全部 provider 失败抛 ServiceError(code="fetch_failed")。
    """
    opts = opts or {}
    fetch_cfg = cfg.get("fetch") or {}
    if not isinstance(fetch_cfg, dict):
        fetch_cfg = {}

    popts = _provider_opts(cfg, fetch_cfg)
    providers = _chain_providers(fetch_cfg, opts, cfgmod.DEFAULTS["fetch"]["providers"])
    reasons: list[str] = []
    result = fetch_chain(url, providers, popts, log=_log_collector(reasons))

    if result is None:
        detail = "; ".join(reasons) or f"providers={providers}"
        raise ServiceError(
            f"all providers failed: {detail}", CATEGORY_ALL_FAILED,
            code="fetch_failed",
        )
    return result


def convert(cfg: dict, path: str, opts: dict | None = None) -> FetchResult:
    """按回退链把本地文件转 Markdown（上传到支持文件转换的服务）。

    文件存在性 / 大小 / 扩展名校验在各 provider 内做（失败为 CATEGORY_INVALID，
    不浪费请求）。不支持文件转换的 provider 会被链自动跳过。
    全部失败抛 ServiceError(code="convert_failed")。
    """
    opts = opts or {}
    conv_cfg = cfg.get("convert") or {}
    if not isinstance(conv_cfg, dict):
        conv_cfg = {}

    popts = _provider_opts(cfg, conv_cfg)
    providers = _chain_providers(conv_cfg, opts, cfgmod.DEFAULTS["convert"]["providers"])
    reasons: list[str] = []
    result = convert_chain(path, providers, popts, log=_log_collector(reasons))

    if result is None:
        detail = "; ".join(reasons) or f"providers={providers}"
        raise ServiceError(
            f"all providers failed: {detail}", CATEGORY_ALL_FAILED,
            code="convert_failed",
        )
    return result


# ── 列表 ───────────────────────────────────────────────────────────────────


def list_providers() -> list[str]:
    return service_names()


def list_convert_providers() -> list[str]:
    return file_convert_services()


def search_backend_names() -> list[str]:
    """search 可用后端（不含 auto）。"""
    return search_services()
