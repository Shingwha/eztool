"""Prioritized fallback chain, shared by URL fetch and file convert.

``run_chain`` calls a named provider method (``fetch`` or
``convert_file``) on each provider in order; on failure it logs the
reason to stderr and moves to the next one. First success wins. A
provider whose method is unsupported (e.g. ``convert_file`` on a
URL-only provider) reports CATEGORY_INVALID and is skipped the same way.
Returns the winning ``FetchResult`` or ``None`` if every provider failed.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from .provider import FetchError, FetchResult, ProviderOpts, create_provider

LogFn = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def run_chain(
    providers: list[str],
    method: str,
    target: str,
    opts: ProviderOpts,
    log: LogFn = _stderr,
) -> Optional[FetchResult]:
    """Run the chain. Never raises for provider failures; if every
    provider fails, returns ``None`` (reasons already logged). Content
    is never truncated — full markdown is always returned."""
    for name in providers:
        try:
            provider = create_provider(name, opts)
        except FetchError as e:  # unknown provider — hard stop, not retriable
            log(f"[{name}] failed: {e}")
            return None

        timeout = provider.timeout(30)
        t0 = time.monotonic()
        try:
            result = getattr(provider, method)(target, timeout=timeout)
        except FetchError as e:
            elapsed = round(time.monotonic() - t0, 3)
            log(f"[{name}] failed: {e} ({elapsed}s) -> next provider")
            continue

        result.elapsed = round(time.monotonic() - t0, 3)
        log(f"[{name}] OK ({result.elapsed}s, {len(result.content)} chars)")
        return result

    log("all providers failed")
    return None


def fetch_chain(
    url: str,
    providers: list[str],
    opts: ProviderOpts,
    log: LogFn = _stderr,
) -> Optional[FetchResult]:
    """URL → Markdown chain (firecrawl → markdown.new → jina by default)."""
    return run_chain(providers, "fetch", url, opts, log)


def convert_chain(
    path: str,
    providers: list[str],
    opts: ProviderOpts,
    log: LogFn = _stderr,
) -> Optional[FetchResult]:
    """Local file → Markdown chain (markdown.new upload by default)."""
    return run_chain(providers, "convert_file", path, opts, log)
