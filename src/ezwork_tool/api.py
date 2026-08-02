"""公共入口：search / fetch / convert + 服务商列表。

统一配置（``providers.<name>`` 段）→ ProviderOpts → 统一回退链。
search 的 ``auto`` 模式是真正的 failover：doubao 失败自动试 deepseek →
anysearch；显式 ``--backend`` 只试该后端。
"""

from __future__ import annotations

import sys

from . import config as cfgmod
from .base import FetchResult, ProviderOpts, SearchResponse
from .chain import convert_chain, fetch_chain, run_chain
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
    "search", "fetch", "convert",
    "list_providers", "list_convert_providers", "search_backend_names",
]

# auto 模式的 failover 顺序（行业惯例：按凭证成本/能力从高到低）
AUTO_SEARCH_ORDER = ["doubao", "deepseek", "anysearch"]

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
    label = "auto" if len(names) > 1 else names[0]
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
    """搜索。backend=auto → AUTO_SEARCH_ORDER 逐个 failover；显式 → 单后端。"""
    opts = opts or {}
    if backend == "auto":
        names = list(AUTO_SEARCH_ORDER)
    else:
        names = [backend]
        if backend not in search_services():
            known = ", ".join(search_services())
            raise UsageError(f"未知后端 '{backend}'（可用: auto, {known}）")
    _check_params(names, opts)

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
