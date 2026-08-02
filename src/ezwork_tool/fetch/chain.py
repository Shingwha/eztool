"""Prioritized fallback chain.

Tries providers in user-configured order; on failure logs the reason to
stderr and moves to the next one. First success wins. Returns the
winning ``FetchResult`` or ``None`` if every provider failed.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from .provider import FetchError, FetchResult, ProviderOpts, create_provider

LogFn = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def fetch_chain(
    url: str,
    providers: list[str],
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
            result = provider.fetch(url, timeout=timeout)
        except FetchError as e:
            elapsed = round(time.monotonic() - t0, 3)
            log(f"[{name}] failed: {e} ({elapsed}s) -> next provider")
            continue

        result.elapsed = round(time.monotonic() - t0, 3)
        log(f"[{name}] OK ({result.elapsed}s, {len(result.content)} chars)")
        return result

    log("all providers failed")
    return None
