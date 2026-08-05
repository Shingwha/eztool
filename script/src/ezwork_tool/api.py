"""公共入口：search_category（按类别路由搜索）/ convert（按输入类型路由转换）。

两种执行模式，同一套 ``--providers`` 参数：
- 不指定 ``--providers``：走 config 回退链（类别内按 priority 排序；跳过
  auth_required 且未配凭证的 provider——匿名可用的不跳）。链配置只存在于
  config（``search.web.providers`` 等），命令行无链概念。
- 指定 ``--providers a,b``：**并行**跑指定 provider（search 合并去重标注来源；
  convert 取先成功者）。指定 1 个 = 单跑（不跳过凭证检查，点名了没配就报错）。
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from . import config as cfgmod
from . import provider as prov
from .provider import FetchResult, ProviderOpts, SearchResponse
from .util import (
    CATEGORY_ALL_FAILED,
    CredentialsError,
    ServiceError,
    UsageError,
)
from . import providers as _providers  # noqa: F401  (side-effect: 注册)

__all__ = ["search_category", "convert", "list_category_providers"]


# ── 配置 → ProviderOpts ────────────────────────────────────────────────────


def _provider_opts(cfg: dict, section: dict) -> ProviderOpts:
    """从 providers.<name> 段构建 ProviderOpts（timeouts + api_keys）。"""
    timeouts: dict = {}
    api_keys: dict = {}
    providers_cfg = cfg.get("providers") or {}
    if not isinstance(providers_cfg, dict):
        providers_cfg = {}
    sec_default = section.get("timeout", 30)
    for name in prov.SERVICES:
        sec = providers_cfg.get(name)
        if not isinstance(sec, dict):
            sec = {}
        to = sec.get("timeout")
        timeouts[name] = to if isinstance(to, int) and to > 0 else sec_default
        key = sec.get("api_key")
        if key:
            api_keys[name] = key
    return ProviderOpts(timeouts=timeouts, api_keys=api_keys)


def _section(cfg: dict, category: str) -> dict:
    """取 ``<域>.<操作>`` 对应的配置段（search.web / convert.page …）。

    返回副本，避免调用方写入（如 _category 标记）污染配置对象。
    """
    domain, _, op = category.partition(".")
    sec = cfg.get(domain) or {}
    if not isinstance(sec, dict):
        sec = {}
    sub = sec.get(op)
    if not isinstance(sub, dict):
        sub = {}
    return dict(sub)


def _chain_providers(section: dict, category: str) -> list[str]:
    """回退链：config 类别段显式配置 > 自动派生默认链。

    命令行 ``--providers`` 不走这里（那是并行名单，见 search_category/convert）。
    """
    providers = section.get("providers")
    if isinstance(providers, list) and providers:
        return providers
    return prov.default_chain(category)


def _check_provider_names(names: list[str]) -> None:
    """未知 provider 名是用法错误（硬停），而不是静默失败。"""
    for n in names:
        if n not in prov.SERVICES:
            raise UsageError(
                f"未知 provider '{n}'（可用: {', '.join(sorted(prov.SERVICES))}）"
            )


def _check_credentials(names: list[str], cfg: dict, popts: ProviderOpts) -> None:
    """显式点名（--providers）的 auth_required provider 必须已配凭证。"""
    for n in names:
        cls = prov.SERVICES[n]
        if cls.auth_required:
            svc = cls(popts)
            if not svc.has_credentials(cfg):
                raise CredentialsError(
                    f"provider '{n}' 需要凭证（eztool config set providers.{n}.api_key ...）"
                )


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _size(result) -> str:
    """log 用的大小描述（兼容 FetchResult / SearchResponse）。"""
    if isinstance(result, FetchResult):
        return f"{len(result.content)} chars"
    if isinstance(result, SearchResponse):
        return f"{len(result.results)} results"
    return ""


# ── search ─────────────────────────────────────────────────────────────────


def search_category(
    cfg: dict, category: str, query: str, opts: dict | None = None,
) -> SearchResponse:
    """按类别路由搜索。无 --providers → 回退链；有 → 并行/单跑。"""
    opts = dict(opts or {})
    section = _section(cfg, category)
    popts = _provider_opts(cfg, section)
    if category == "search.image":
        opts["image"] = True  # doubao 图片模式开关（provider 方法零改动）

    names = _parse_providers(opts)
    if names:  # 显式并行/单跑
        _check_provider_names(names)
        _check_credentials(names, cfg, popts)
        resp = _search_parallel(cfg, category, query, opts, names, popts)
    else:  # 回退链（自动跳过未配凭证的 auth_required provider）
        names = [n for n in _chain_providers(section, category)
                 if not (prov.SERVICES[n].auth_required
                         and not prov.SERVICES[n](popts).has_credentials(cfg))]
        _check_provider_names(names)
        resp = _search_chain(cfg, category, query, opts, names, popts)
    if resp.metadata is None:
        resp.metadata = {}
    return resp


def _parse_providers(opts: dict) -> list[str]:
    raw = opts.get("providers")
    if isinstance(raw, str) and raw.strip():
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _search_chain(cfg, category, query, opts, names, popts) -> SearchResponse:
    """回退链：按序尝试，第一个成功即返回（失败自动换下一个）。"""
    backup: SearchResponse | None = None
    for name in names:
        svc = prov.SERVICES[name](popts)
        t0 = _now()
        try:
            resp = svc.search(cfg, query, opts)
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
        return name, svc.search(cfg, query, opts)

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

    resp = SearchResponse(
        query=query, results=merged,
        answer="\n\n".join(a for a in answers if a) or None,
        metadata={"backend": ",".join(ok_names)},
    )
    return resp


def _raise_all_failed(kind: str, names: list[str]) -> None:
    raise ServiceError(
        f"all providers failed: providers={names}", CATEGORY_ALL_FAILED,
        code=f"{kind}_failed",
    )


def _now() -> float:
    return time.monotonic()


def _elapsed(t0: float) -> str:
    return f"{round(time.monotonic() - t0, 3)}"


# ── convert ────────────────────────────────────────────────────────────────


def convert(cfg: dict, target: str, opts: dict | None = None) -> FetchResult:
    """按输入类型路由转换：URL → convert.page 链；本地路径 → convert.file 链。

    无 --providers → 回退链；有 → 并行/单跑（convert 并行 = 全部启动，取
    第一个成功者——同一 URL 内容等价，不需要合并多条）。
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
    names = _parse_providers(opts)
    if names:
        _check_provider_names(names)
        _check_credentials(names, cfg, popts)
        fetched = _convert_parallel(names, category, invoke, popts)
    else:
        chain = [n for n in _chain_providers(section, category)
                 if not (prov.SERVICES[n].auth_required
                         and not prov.SERVICES[n](popts).has_credentials(cfg))]
        _check_provider_names(chain)
        fetched = _convert_chain(chain, category, invoke, popts)

    if getattr(fetched, "low_quality", False):
        print(
            f"warning: 内容可疑（命中拦截话术: {fetched.quality_reason or 'unknown'}），"
            f"可能不完整；可换 provider 重试（--providers ...）",
            file=sys.stderr,
        )
    return fetched


def _convert_chain(names, category, invoke, popts) -> FetchResult:
    backup: FetchResult | None = None
    for name in names:
        svc = prov.SERVICES[name](popts)
        t0 = _now()
        try:
            result = invoke(svc)
        except ServiceError as e:
            _log(f"[{name}] failed: {e} ({_elapsed(t0)}s) -> next provider")
            continue
        if getattr(result, "low_quality", False):
            reason = getattr(result, "quality_reason", "") or "low quality"
            _log(f"[{name}] suspicious ({reason}, {_size(result)}) -> keep as backup")
            if backup is None:
                backup = result
            continue
        _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(result)})")
        return result
    if backup is not None:
        _log("all providers failed or suspicious; returning best backup")
        return backup
    _raise_all_failed("convert", names)


def _convert_parallel(names, category, invoke, popts) -> FetchResult:
    """并行启动全部指定 provider，取第一个成功者；全失败才报错。"""
    def run(name: str):
        svc = prov.SERVICES[name](popts)
        return invoke(svc)

    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        futures = {ex.submit(run, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            t0 = _now()
            try:
                result = fut.result()
            except ServiceError as e:
                _log(f"[{name}] failed: {e} ({_elapsed(t0)}s)")
                continue
            _log(f"[{name}] OK ({_elapsed(t0)}s, {_size(result)})")
            return result  # 第一个成功即收
    _raise_all_failed("convert", names)


# ── 列表 ───────────────────────────────────────────────────────────────────


def list_category_providers(category: str, cfg: dict | None = None) -> list[str]:
    """指定类别的 provider 候选（注册顺序）。"""
    return prov.providers_for(category)


def list_providers() -> list[str]:
    """全部 provider 名（排序）。"""
    return sorted(prov.SERVICES)
