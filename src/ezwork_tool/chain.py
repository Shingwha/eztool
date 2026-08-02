"""统一回退链（failover）：按序尝试服务商，ServiceError 记录后继续。

行业惯例：可重试错误（timeout/network/http/empty）换下一个服务商；
不可重试（invalid/auth/no_results）同样记录后跳过（下一个可能有不同
能力或匿名可用）；未知服务商名是配置错误，硬停止。全部失败返回 None
（原因已 log 到 stderr）。第一个成功即返回，绝不截断内容。
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from .base import FetchResult, ProviderOpts, SearchResponse
from .errors import ServiceError
from .registry import create_service

LogFn = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _size(result) -> str:
    """log 用的大小描述（兼容 FetchResult / SearchResponse）。"""
    if isinstance(result, FetchResult):
        return f"{len(result.content)} chars"
    if isinstance(result, SearchResponse):
        return f"{len(result.results)} results"
    return ""


def run_chain(
    names: list[str],
    capability: str,
    invoke: Callable,
    opts: ProviderOpts | None = None,
    log: LogFn = _stderr,
) -> Optional[tuple]:
    """按序尝试。``invoke(service)`` 返回结果或抛 ServiceError。

    返回 ``(result, provider_name)``；全部失败 / 未知 provider 返回 None。
    """
    for name in names:
        try:
            svc = create_service(name, opts)
        except ServiceError as e:  # unknown provider — hard stop, not retriable
            log(f"[{name}] failed: {e}")
            return None

        if capability not in svc.capabilities:
            log(f"[{name}] skipped: no '{capability}' capability")
            continue

        t0 = time.monotonic()
        try:
            result = invoke(svc)
        except ServiceError as e:
            elapsed = round(time.monotonic() - t0, 3)
            log(f"[{name}] failed: {e} ({elapsed}s) -> next provider")
            continue

        elapsed = round(time.monotonic() - t0, 3)
        log(f"[{name}] OK ({elapsed}s, {_size(result)})")
        return result, name

    log("all providers failed")
    return None


def fetch_chain(
    url: str,
    providers: list[str],
    opts: ProviderOpts,
    log: LogFn = _stderr,
) -> Optional[FetchResult]:
    """URL → Markdown 链（firecrawl → markdown.new → jina by default）。

    返回 FetchResult 或 None（保持历史签名；FetchResult.provider 含服务商名）。
    """
    result = run_chain(
        providers, "fetch",
        lambda svc: svc.fetch(url, timeout=svc.timeout(30)),
        opts, log,
    )
    return result[0] if result else None


def convert_chain(
    path: str,
    providers: list[str],
    opts: ProviderOpts,
    log: LogFn = _stderr,
) -> Optional[FetchResult]:
    """Local file → Markdown chain（markdown.new upload by default）。"""
    result = run_chain(
        providers, "convert_file",
        lambda svc: svc.convert_file(path, timeout=svc.timeout(60)),
        opts, log,
    )
    return result[0] if result else None
