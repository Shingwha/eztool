"""公共入口：search（按类别路由）/ fetch（URL→Markdown）/ convert（本地文件→Markdown）。

执行语义（与命令面对齐）：

- 缺省：走 ``chains.<类别>`` 回退链（串行，第一个成功即返回；自动跳过
  auth_required 且未配凭证的 provider）。
- search ``--use a,b`` / ``--all``：**并行**跑多个 provider，结果按 URL
  去重合并、标注来源（``--all`` = 该类别默认链全员并行）。
- fetch/convert ``--use a,b``：**顺序覆盖链**——按给定顺序串行试
  （同一内容不重复打 API）。显式点名的 auth_required provider 未配凭证
  直接报错，不静默跳过。

内容质量门（拦截页/可疑内容）统一收口在 ``_convert_chain`。

``--summarize``（search/fetch/convert 通用）：内容拿到后再过一道
``summarize`` 模块做 AI 提炼——search 回填 ``resp.answer`` + ``citations``
（引用表程序生成），pages 由 ``summarize_pages`` 返回 Summary；LLM 失败
降级为原始结果（warning），未配 ``summarize.*`` 则用法错误（exit 2）。
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config as cfgmod
from . import provider as prov
from . import summarize as smm
from .provider import FetchResult, ProviderOpts, SearchResponse, SearchResult
from .util import (
    CATEGORY_ALL_FAILED,
    CredentialsError,
    ServiceError,
    UsageError,
    assess_content,
)
from . import providers as _providers  # noqa: F401  (side-effect: 注册)

__all__ = ["search", "fetch", "fetch_many", "convert", "summarize_pages",
           "check_summarize", "list_category_providers", "list_sources"]


# ── 配置 → ProviderOpts ──────────────────────────────────────────────────────


def _provider_opts(cfg: dict, cli_timeout: int | None = None) -> ProviderOpts:
    """从 cfg 构建 ProviderOpts（超时的唯一解析点 + 凭证/私有配置的唯一来源）。

    超时优先级：``--timeout`` > ``providers.<name>.timeout`` > ``settings.timeout``。
    """
    settings_timeout = cfgmod.get_key(cfg, "settings.timeout", 30) or 30
    timeouts: dict = {}
    configs: dict = {}
    providers_cfg = cfg.get("providers") or {}
    if not isinstance(providers_cfg, dict):
        providers_cfg = {}
    for name in prov.SERVICES:
        sec = providers_cfg.get(name)
        if not isinstance(sec, dict):
            sec = {}
        timeouts[name] = cli_timeout or sec.get("timeout") or settings_timeout
        configs[name] = {k: v for k, v in sec.items() if k != "timeout" and v is not None}
    return ProviderOpts(timeouts=timeouts, configs=configs)


def _parse_use(raw) -> list[str]:
    """``--use a,b`` → provider 名单（空 = 未指定）。"""
    if isinstance(raw, str) and raw.strip():
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _check_provider_names(names: list[str]) -> None:
    """未知 provider 名是用法错误（硬停），而不是静默失败。"""
    for n in names:
        if n not in prov.SERVICES:
            raise UsageError(
                f"unknown provider '{n}' (available: {', '.join(sorted(prov.SERVICES))})"
            )


def _check_credentials(names: list[str], popts: ProviderOpts) -> None:
    """显式点名（--use）的 auth_required provider 必须已配凭证。"""
    for n in names:
        cls = prov.SERVICES[n]
        if cls.auth_required and not cls(popts).has_credentials():
            raise CredentialsError(
                f"provider '{n}' requires credentials "
                f"(run: eztool config set providers.{n}.api_key ...)"
            )


def _credentialed_chain(cfg: dict, category: str, popts: ProviderOpts) -> list[str]:
    """配置回退链，过滤掉 auth_required 但未配凭证的 provider（匿名可用的不跳）。"""
    chain = cfgmod.get_key(cfg, f"chains.{category}") or prov.default_chain(category)
    _check_provider_names(chain)
    return [
        n for n in chain
        if not (prov.SERVICES[n].auth_required
                and not prov.SERVICES[n](popts).has_credentials())
    ]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _size(result) -> str:
    """log 用的大小描述（兼容 FetchResult / SearchResponse）。"""
    if isinstance(result, FetchResult):
        return f"{len(result.content)} chars"
    if isinstance(result, SearchResponse):
        return f"{len(result.results)} results"
    return ""


def _now() -> float:
    return time.monotonic()


def _elapsed(t0: float) -> str:
    return f"{round(time.monotonic() - t0, 3)}"


def _raise_all_failed(kind: str, names: list[str]) -> None:
    raise ServiceError(
        f"all providers failed: providers={names}", CATEGORY_ALL_FAILED,
        code=f"{kind}_failed",
    )


# ── search ───────────────────────────────────────────────────────────────────


def search(
    cfg: dict, category: str, query: str, opts: dict | None = None,
) -> SearchResponse:
    """按类别路由搜索。无 --use/--all → 回退链；有 → 并行合并。"""
    opts = dict(opts or {})
    popts = _provider_opts(cfg, opts.pop("timeout", None))
    use = _parse_use(opts.pop("use", None))
    run_all = bool(opts.pop("all", False))
    want_summary = bool(opts.pop("summarize", False))

    if use:  # 显式并行/单跑（不跳过凭证检查，点名了没配就报错）
        _check_provider_names(use)
        _check_credentials(use, popts)
        resp = _search_parallel(cfg, category, query, opts, use, popts)
    else:
        names = _credentialed_chain(cfg, category, popts)
        if run_all:  # --all：默认链全员并行
            resp = _search_parallel(cfg, category, query, opts, names, popts)
        else:
            resp = _search_chain(cfg, category, query, opts, names, popts)
    if resp.metadata is None:
        resp.metadata = {}
    if want_summary:
        _apply_search_summary(cfg, query, resp)
    return resp


def _apply_search_summary(cfg: dict, query: str, resp: SearchResponse) -> None:
    """--summarize：结果喂 LLM 提炼，回填 answer + 确定性引用表。

    LLM 失败降级为原始结果（metadata.summary_error 记录原因）；未配
    summarize.* 抛 UsageError（exit 2，config 前置校验一般已拦住）。
    """
    backend = str((resp.metadata or {}).get("backend") or "")
    items = [
        smm.SourceItem(
            title=r.title, url=r.url,
            text=r.content or r.snippet or "",
            provider=r.source or backend,
        )
        for r in resp.results
    ]
    t0 = _now()
    try:
        summary = smm.summarize(cfg, query, items)
    except ServiceError as e:
        resp.metadata["summary_error"] = str(e)
        _log(f"[summarize] failed: {e} -> returning raw results")
        return
    resp.answer = summary.answer
    resp.citations = summary.citations
    _log(f"[summarize] OK ({_elapsed(t0)}s, {len(summary.citations)} sources)")


def _search_chain(cfg, category, query, opts, names, popts) -> SearchResponse:
    """回退链：按序尝试，第一个成功即返回（失败自动换下一个）。"""
    for name in names:
        svc = prov.SERVICES[name](popts)
        t0 = _now()
        try:
            resp = svc.search(category, query, opts)
        except ServiceError as e:
            _log(f"[{name}] failed: {e} ({_elapsed(t0)}s) -> next provider")
            continue
        if resp.metadata is None:
            resp.metadata = {}
        resp.metadata.setdefault("backend", name)
        _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(resp)})")
        return resp
    _raise_all_failed("search", names)


def _search_parallel(cfg, category, query, opts, names, popts) -> SearchResponse:
    """并行跑指定 provider，结果按 URL 去重合并，标注来源。单个失败不影响其他。"""
    results: list[SearchResult] = []
    answers: list[str] = []
    ok_names: list[str] = []

    def run(name: str):
        svc = prov.SERVICES[name](popts)
        return name, svc.search(category, query, opts)

    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        futures = {ex.submit(run, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            t0 = _now()
            try:
                _, resp = fut.result()
            except ServiceError as e:
                _log(f"[{name}] failed: {e} ({_elapsed(t0)}s)")
                continue
            ok_names.append(name)
            if resp.answer:
                answers.append(resp.answer)
            for r in resp.results or []:
                r.source = name
                results.append(r)
            _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(resp)})")

    if not ok_names:
        _raise_all_failed("search", names)

    merged: list[SearchResult] = []
    seen: set[str] = set()
    for r in results:
        key = r.url or r.title
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(r)

    return SearchResponse(
        query=query, results=merged,
        answer="\n\n".join(a for a in answers if a) or None,
        metadata={"backend": ",".join(ok_names)},
    )


# ── fetch / convert ──────────────────────────────────────────────────────────


def fetch(cfg: dict, url: str, opts: dict | None = None) -> FetchResult:
    """URL → Markdown（page 链）。无 --use → 配置链；有 → 顺序覆盖链。"""
    return _convert(cfg, "page", opts,
                    lambda svc: svc.fetch(url, timeout=svc.timeout()))


def fetch_many(
    cfg: dict, urls: list[str], opts: dict | None = None
) -> tuple[list[FetchResult], list[tuple[str, ServiceError]]]:
    """多 URL 抓取：URL 间并行，每个 URL 独立走 page 链（质量门/回退不变）。

    返回 ``(results, errors)``——results 按输入顺序；单个 URL 失败不拖死
    整批（收进 errors），全部失败才抛 ALL_FAILED。
    """
    opts = opts or {}
    popts = _provider_opts(cfg, opts.get("timeout"))
    use = _parse_use(opts.get("use"))
    if use:
        _check_provider_names(use)
        _check_credentials(use, popts)
        names = use
    else:
        names = _credentialed_chain(cfg, "page", popts)

    def run(url: str) -> FetchResult:
        return _convert_chain(
            names, lambda svc: svc.fetch(url, timeout=svc.timeout()), popts
        )

    by_url: dict[str, FetchResult] = {}
    errors: list[tuple[str, ServiceError]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as ex:
        futures = {ex.submit(run, u): u for u in urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                by_url[url] = fut.result()
            except ServiceError as e:
                _log(f"[fetch] {url} failed: {e}")
                errors.append((url, e))
    results = [by_url[u] for u in urls if u in by_url]
    if not results:
        _raise_all_failed("fetch", urls)
    return results, errors


def convert(cfg: dict, path: str, opts: dict | None = None) -> FetchResult:
    """本地文件 → Markdown（file 链）。无 --use → 配置链；有 → 顺序覆盖链。"""
    if not os.path.exists(path):
        raise UsageError(f"file not found: {path}")
    return _convert(cfg, "file", opts,
                    lambda svc: svc.convert_file(path, timeout=svc.timeout()))


def _convert(cfg: dict, category: str, opts: dict | None, invoke) -> FetchResult:
    opts = opts or {}
    popts = _provider_opts(cfg, opts.get("timeout"))
    use = _parse_use(opts.get("use"))
    if use:
        _check_provider_names(use)
        _check_credentials(use, popts)
        names = use
    else:
        names = _credentialed_chain(cfg, category, popts)
    return _convert_chain(names, invoke, popts)


def _convert_chain(names, invoke, popts) -> FetchResult:
    """串行链 + 质量门：拦截页当失败继续；可疑内容留 backup；全失败取 backup。"""
    backup: FetchResult | None = None
    for name in names:
        svc = prov.SERVICES[name](popts)
        t0 = _now()
        try:
            result = invoke(svc)
        except ServiceError as e:
            _log(f"[{name}] failed: {e} ({_elapsed(t0)}s) -> next provider")
            continue
        q = assess_content(result.content)
        if not q.ok:  # 拦截/验证页（假成功）：换下一个
            _log(f"[{name}] blocked (hits: {', '.join(q.hits)}, "
                 f"{_size(result)}) -> next provider")
            continue
        if q.low_quality:  # 可疑但可能真实：留作兜底，继续找更好的
            _log(f"[{name}] suspicious ({', '.join(q.hits)}, "
                 f"{_size(result)}) -> keep as backup")
            if backup is None:
                backup = result
            continue
        _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(result)})")
        return result
    if backup is not None:
        _log("all providers failed or suspicious; returning best backup")
        print(
            f"warning: content looks suspicious (matched blocking phrases: "
            f"{', '.join(assess_content(backup.content).hits)}); it may be incomplete "
            f"— retry with another provider (--use ...)",
            file=sys.stderr,
        )
        return backup
    _raise_all_failed("convert", names)


# ── summarize（AI 提炼钩子）─────────────────────────────────────────────────


def check_summarize(cfg: dict) -> None:
    """--summarize 前置校验：缺配置立即 UsageError（exit 2），不浪费搜索/抓取。"""
    smm.resolve_config(cfg)


def summarize_pages(cfg: dict, request: str, results: list[FetchResult]) -> smm.Summary:
    """fetch/convert 的 --summarize：抓取内容喂 LLM 提炼（引用按 URL 编号）。"""
    items = [
        smm.SourceItem(title=r.url, url=r.url, text=r.content, provider=r.provider)
        for r in results
    ]
    t0 = _now()
    summary = smm.summarize(cfg, request, items)
    _log(f"[summarize] OK ({_elapsed(t0)}s, {len(summary.citations)} sources)")
    return summary


# ── 列表 ─────────────────────────────────────────────────────────────────────


def list_category_providers(category: str) -> list[str]:
    """指定类别的 provider 候选（注册顺序）。"""
    return prov.providers_for(category)


def list_providers() -> list[str]:
    """全部 provider 名（排序）。"""
    return sorted(prov.SERVICES)


def list_sources() -> list[tuple[str, str]]:
    """全部数据源标签（注册表聚合，``eztool sources`` 输出）。"""
    return prov.all_sources()
