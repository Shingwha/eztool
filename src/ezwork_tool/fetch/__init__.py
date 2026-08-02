"""抓取统一入口：cfg["fetch"] 段 → ProviderOpts → fetch_chain。

- fetch(cfg, url, opts)：按回退链（firecrawl → markdown.new → jina）抓取 URL 转 Markdown
- list_providers()：已注册的 provider 名
"""

from __future__ import annotations

import sys

from ..errors import BackendError
from . import providers as _providers  # noqa: F401  (side-effect: register)
from .chain import fetch_chain
from .provider import FetchError, FetchResult, ProviderOpts, provider_names

__all__ = ["fetch", "list_providers", "FetchResult"]

# 各 provider 段缺省超时（与 config.DEFAULTS 一致）；未配置段的 provider 回退全局超时
DEFAULT_TIMEOUTS = {"firecrawl": 60, "markdown": 30, "jina": 10}
DEFAULT_PROVIDERS = ["firecrawl", "markdown", "jina"]


def _global_timeout(fetch_cfg: dict, opts: dict) -> int:
    """opts.timeout 覆盖 cfg["fetch"]["timeout"]，默认 30。"""
    to = opts.get("timeout")
    if not isinstance(to, int):
        to = fetch_cfg.get("timeout", 30)
    return to if isinstance(to, int) and to > 0 else 30


def _provider_opts(fetch_cfg: dict, global_timeout: int) -> ProviderOpts:
    """从 cfg["fetch"] 构建 ProviderOpts。

    timeouts：按各 provider 段（firecrawl.timeout 默认 60 / markdown 30 / jina 10），
    段缺失或非正数时用段缺省，仍缺省则回退全局超时。
    api_keys：从 firecrawl.api_key / jina.api_key 取（None 就无 key）。
    """
    timeouts: dict = {}
    api_keys: dict = {}
    for name in provider_names():
        sec = fetch_cfg.get(name)
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

    raw = opts.get("providers")
    if isinstance(raw, str):
        providers = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        providers = fetch_cfg.get("providers")
    if not isinstance(providers, list) or not providers:
        providers = list(DEFAULT_PROVIDERS)

    reasons: list[str] = []

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)  # stderr 日志原样保留
        reasons.append(msg)

    try:
        result = fetch_chain(url, providers, popts, log=log)
    except FetchError as e:  # 防御：fetch_chain 自身不抛 FetchError
        raise BackendError(f"fetch failed: {e}", code="fetch_failed") from e

    if result is None:
        detail = "; ".join(reasons) or f"providers={providers}"
        raise BackendError(f"all providers failed: {detail}", code="fetch_failed")
    return result


def list_providers() -> list[str]:
    return provider_names()
