"""公共入口：search（按类别路由）/ fetch（URL→Markdown）/ convert（本地文件→Markdown）。

执行语义（与命令面对齐）：

- search 缺省：走 ``chains.web`` 回退链（串行，第一个成功即返回；自动跳过
  auth_required 且未配凭证的 provider）。
- search ``--use a,b``：点名多个才**并行**，公平轮转去重合并并标注来源，
  结果总数受安全阀上限保护。
- search ``--max N``：**逐家升级**——沿链（或 ``--use`` 名单）顺序询问，
  去重累计达到 N 即停手（最后一家自然超出，不修剪）。
- fetch/convert ``--use a,b``：**顺序覆盖链**——按给定顺序串行试
  （同一内容不重复打 API）。显式点名的 auth_required provider 未配凭证
  直接报错，不静默跳过。

内容质量门（拦截页/可疑内容）统一收口在 ``_convert_chain``。

搜索超时：任一显式配置（``--timeout`` / ``providers.<n>.timeout`` /
``settings.timeout``）照常生效；全都没配时 web 搜索用快速缺省
（``SEARCH_DEFAULT_TIMEOUT``），抓取/转换不受影响。

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
           "check_summarize", "list_category_providers", "list_providers"]


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
    """配置回退链，过滤未知 provider 名（旧配置残留，警告后剔除）与
    auth_required 但未配凭证的 provider（匿名可用的不跳）。"""
    chain = cfgmod.get_key(cfg, f"chains.{category}") or prov.default_chain(category)
    known = [n for n in chain if n in prov.SERVICES]
    unknown = [n for n in chain if n not in prov.SERVICES]
    if unknown:
        _log(f"warning: chains.{category} contains unknown providers "
             f"(removed): {', '.join(unknown)}")
    return [
        n for n in known
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

MERGED_HARD_CAP = 40        # 多名并行合并的安全阀（无 --max 的路径）
SEARCH_DEFAULT_TIMEOUT = 10  # 零显式超时配置时，web 搜索的快速缺省


def search(
    cfg: dict, category: str, query: str, opts: dict | None = None,
) -> SearchResponse:
    """按类别路由搜索。

    - 缺省：回退链，第一家成功即返回。
    - ``--use A,B``：全员并行，公平轮转去重合并（安全阀 ``MERGED_HARD_CAP``）。
    - ``--max N``：沿名单逐家升级，去重累计 ≥N 即停手（最后一家可自然超出；
      缺省链与点名名单皆适用）。

    opts 只认保留键（``use`` / ``max`` / ``timeout`` / ``summarize`` /
    ``fast_timeout_default``），出现未知键是用法错误——不留静默透传通道。
    """
    opts = dict(opts or {})
    fast_default = bool(opts.pop("fast_timeout_default", True))
    popts = _search_provider_opts(cfg, opts.pop("timeout", None), fast_default)
    use = _parse_use(opts.pop("use", None))
    target = _parse_max(opts.pop("max", None))
    want_summary = bool(opts.pop("summarize", False))
    if opts:
        raise UsageError(f"unknown search option(s): {', '.join(sorted(opts))}")

    if use:
        _check_provider_names(use)
        _check_credentials(use, popts)
        names = use
    else:
        names = _credentialed_chain(cfg, category, popts)

    if target:
        resp = _search_escalate(category, query, opts, names, popts, target)
    elif use and len(use) > 1:
        resp = _search_parallel(category, query, opts, names, popts)
    else:
        resp = _search_chain(category, query, opts, names, popts)
    if resp.metadata is None:
        resp.metadata = {}
    if want_summary:
        _apply_search_summary(cfg, query, resp)
    return resp


def _parse_max(raw) -> int | None:
    """``--max`` → 目标条数；非法值是用法错误。"""
    if raw is None:
        return None
    try:
        target = int(raw)
    except (TypeError, ValueError):
        raise UsageError(f"--max expects an integer, got: {raw!r}") from None
    if target < 1:
        raise UsageError("--max must be >= 1")
    return target


def _search_provider_opts(
    cfg: dict, cli_timeout: int | None, fast_default: bool = True,
) -> ProviderOpts:
    """搜索专用 ProviderOpts：零显式超时配置时用快速缺省。

    显式优先级不变（``cli_timeout`` > ``providers.<n>.timeout`` >
    ``settings.timeout``）；三者都未涉足时（``fast_default=True``，
    由 CLI 读稀疏配置文件判断），把 web 类别中没有独立 timeout 的
    provider 覆写为 ``SEARCH_DEFAULT_TIMEOUT``——搜索不必久等，
    fetch/convert 各自的解析路径不受影响。
    """
    popts = _provider_opts(cfg, cli_timeout)
    if cli_timeout or not fast_default:
        return popts
    secs = cfg.get("providers") or {}
    for name in prov.providers_for("web"):
        sec = secs.get(name)
        if isinstance(sec, dict) and sec.get("timeout"):
            continue
        popts.timeouts[name] = SEARCH_DEFAULT_TIMEOUT
    return popts


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


def _search_chain(category, query, opts, names, popts) -> SearchResponse:
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


def _tag_results(resp, name: str) -> list[SearchResult]:
    """给一份搜索结果的每条打上来源标注。"""
    rows = []
    for r in resp.results or []:
        r.source = name
        rows.append(r)
    return rows


def _dedup_round_robin(
    buckets: dict[str, list[SearchResult]], order: list[str], cap: int | None = None,
) -> tuple[list[SearchResult], int]:
    """公平轮转合并：按 ``order`` 每家轮流出一条，key（URL，缺失回退标题）
    首见者入选、其余丢弃；到 ``cap`` 即提前收工。

    返回 ``(merged, available)``——available 是全部候选条数，供调用方判断
    是否发生了截断。
    """
    merged: list[SearchResult] = []
    seen: set[str] = set()
    available = sum(len(v) for v in buckets.values())
    depth = 0
    progressing = True
    while progressing:
        progressing = False
        for name in order:
            bucket = buckets.get(name) or []
            if depth >= len(bucket):
                continue
            progressing = True
            if cap is not None and len(merged) >= cap:
                return merged, available
            r = bucket[depth]
            key = r.url or r.title
            if key:
                if key in seen:
                    continue
                seen.add(key)
            merged.append(r)
        depth += 1
    return merged, available


def _search_parallel(category, query, opts, names, popts) -> SearchResponse:
    """点名并行：全员同发，公平轮转去重合并（安全阀 MERGED_HARD_CAP）。

    单家失败不影响其余；全部失败才抛 ALL_FAILED。结果顺序由名单顺序
    决定（不随线程完成时序抖动）。
    """
    buckets: dict[str, list[SearchResult]] = {}
    answers: list[str] = []

    def run(name: str):
        svc = prov.SERVICES[name](popts)
        return svc.search(category, query, opts)

    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        futures = {ex.submit(run, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            t0 = _now()
            try:
                resp = fut.result()
            except ServiceError as e:
                _log(f"[{name}] failed: {e} ({_elapsed(t0)}s)")
                continue
            if resp.answer:
                answers.append(resp.answer)
            buckets[name] = _tag_results(resp, name)
            _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(resp)})")

    if not buckets:
        _raise_all_failed("search", names)

    merged, available = _dedup_round_robin(buckets, names, cap=MERGED_HARD_CAP)
    metadata = {"backend": ",".join(n for n in names if n in buckets)}
    if available > len(merged):
        metadata["truncated"] = True
        _log(f"[merge] capped at {len(merged)} "
             f"({available - len(merged)} candidates dropped; narrow --use)")
    return SearchResponse(
        query=query, results=merged,
        answer="\n\n".join(a for a in answers if a) or None,
        metadata=metadata,
    )


def _search_escalate(
    category, query, opts, names, popts, target: int,
) -> SearchResponse:
    """``--max`` 升级链：沿名单逐家询问，去重累计达到 target 即停手。

    最后一家自然超出不修剪；名单耗尽仍不足则带已有结果返回。
    """
    buckets: dict[str, list[SearchResult]] = {}
    answers: list[str] = []
    served: list[str] = []
    merged: list[SearchResult] = []

    for name in names:
        svc = prov.SERVICES[name](popts)
        t0 = _now()
        try:
            resp = svc.search(category, query, opts)
        except ServiceError as e:
            _log(f"[{name}] failed: {e} ({_elapsed(t0)}s) -> next provider")
            continue
        if resp.answer:
            answers.append(resp.answer)
        buckets[name] = _tag_results(resp, name)
        served.append(name)
        merged, _ = _dedup_round_robin(buckets, names)
        _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(resp)}, "
             f"{len(merged)}/{target})")
        if len(merged) >= target:
            break

    if not served:
        _raise_all_failed("search", names)
    return SearchResponse(
        query=query, results=merged,
        answer="\n\n".join(a for a in answers if a) or None,
        metadata={"backend": ",".join(served)},
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
