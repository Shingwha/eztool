"""统一入口：cfg 段 → ProviderOpts → 回退链。

- fetch(cfg, url, opts)：按回退链（firecrawl → markdown.new → jina）抓取 URL 转 Markdown
- convert(cfg, path, opts)：按回退链（markdown.new 上传）把本地文件转 Markdown
- list_providers() / list_convert_providers()：已注册的 provider 名
"""

from __future__ import annotations

import sys

from ..errors import BackendError
from . import providers as _providers  # noqa: F401  (side-effect: register)
from .chain import convert_chain, fetch_chain
from .provider import (
    FetchError,
    FetchResult,
    ProviderOpts,
    file_convert_providers,
    provider_names,
)

__all__ = ["fetch", "convert", "list_providers", "list_convert_providers", "FetchResult"]

# 各 provider 段缺省超时（与 config.DEFAULTS 一致）；未配置段的 provider 回退全局超时
DEFAULT_TIMEOUTS = {"firecrawl": 60, "markdown": 30, "jina": 10}
DEFAULT_PROVIDERS = ["firecrawl", "markdown", "jina"]
DEFAULT_CONVERT_PROVIDERS = ["markdown"]


def _global_timeout(cfg_section: dict, opts: dict) -> int:
    """opts.timeout 覆盖 cfg 段的 timeout，默认 30。"""
    to = opts.get("timeout")
    if not isinstance(to, int):
        to = cfg_section.get("timeout", 30)
    return to if isinstance(to, int) and to > 0 else 30


def _provider_opts(cfg_section: dict, global_timeout: int) -> ProviderOpts:
    """从配置段（fetch 或 convert）构建 ProviderOpts。

    timeouts：按各 provider 子段（如 fetch.firecrawl.timeout）取值，
    子段缺失或非正数时用段缺省，仍缺省则回退全局超时。
    api_keys：从各 provider 子段的 api_key 取（None 就无 key）。
    """
    timeouts: dict = {}
    api_keys: dict = {}
    for name in provider_names():
        sec = cfg_section.get(name)
        if not isinstance(sec, dict):
            sec = {}
        to = sec.get("timeout")
        if isinstance(to, int) and to > 0:
            timeouts[name] = to
        else:
            timeouts[name] = DEFAULT_TIMEOUTS.get(name, global_timeout)
        key = sec.get("api_key")
        if key:
            api_keys[name] = key
    return ProviderOpts(timeouts=timeouts, api_keys=api_keys)


def _chain_providers(cfg_section: dict, opts: dict, defaults: list[str]) -> list[str]:
    """回退链：opts.providers（逗号分隔）覆盖配置，再回退默认。"""
    raw = opts.get("providers")
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    providers = cfg_section.get("providers")
    if not isinstance(providers, list) or not providers:
        return list(defaults)
    return providers


def fetch(cfg: dict, url: str, opts: dict | None = None) -> FetchResult:
    """按回退链抓取 URL 转 Markdown。

    opts 只取：
      - timeout (int|None)：覆盖 cfg["fetch"]["timeout"]（默认 30）
      - providers (str|None)：逗号分隔字符串，覆盖 cfg["fetch"]["providers"]

    返回 FetchResult{provider, content, url, elapsed, tokens}（原结构）。
    全部 provider 失败时抛 BackendError(message, code="fetch_failed")；
    message 汇总各 provider 失败原因（chain 的 stderr 日志照常打印明细）。
    """
    opts = opts or {}
    fetch_cfg = cfg.get("fetch") or {}
    if not isinstance(fetch_cfg, dict):
        fetch_cfg = {}

    popts = _provider_opts(fetch_cfg, _global_timeout(fetch_cfg, opts))
    providers = _chain_providers(fetch_cfg, opts, DEFAULT_PROVIDERS)

    reasons: list[str] = []

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)  # stderr 日志原样保留
        reasons.append(msg)

    try:
        result = fetch_chain(url, providers, popts, log=log)
    except FetchError as e:  # 防御：chain 自身不抛 FetchError
        raise BackendError(f"fetch failed: {e}", code="fetch_failed") from e

    if result is None:
        detail = "; ".join(reasons) or f"providers={providers}"
        raise BackendError(f"all providers failed: {detail}", code="fetch_failed")
    return result


def convert(cfg: dict, path: str, opts: dict | None = None) -> FetchResult:
    """按回退链把本地文件转 Markdown（上传到支持文件转换的服务）。

    opts 只取：
      - timeout (int|None)：覆盖 cfg["convert"]["timeout"]（默认 60）
      - providers (str|None)：逗号分隔字符串，覆盖 cfg["convert"]["providers"]

    文件存在性 / 大小 / 扩展名校验在各 provider 内做（失败为
    CATEGORY_INVALID，不浪费请求）。不支持文件转换的 provider 会被链自动跳过。
    全部失败时抛 BackendError(message, code="convert_failed")。
    """
    opts = opts or {}
    conv_cfg = cfg.get("convert") or {}
    if not isinstance(conv_cfg, dict):
        conv_cfg = {}

    popts = _provider_opts(conv_cfg, _global_timeout(conv_cfg, opts))
    providers = _chain_providers(conv_cfg, opts, DEFAULT_CONVERT_PROVIDERS)

    reasons: list[str] = []

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)
        reasons.append(msg)

    try:
        result = convert_chain(path, providers, popts, log=log)
    except FetchError as e:  # 防御：chain 自身不抛 FetchError
        raise BackendError(f"convert failed: {e}", code="convert_failed") from e

    if result is None:
        detail = "; ".join(reasons) or f"providers={providers}"
        raise BackendError(f"all providers failed: {detail}", code="convert_failed")
    return result


def list_providers() -> list[str]:
    return provider_names()


def list_convert_providers() -> list[str]:
    return file_convert_providers()
