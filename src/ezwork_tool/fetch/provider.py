"""Provider protocol, error taxonomy, and the registry.

This module is the extension point of ezwork-fetch. To add a new free
fetch service:

    1. Create ``providers/my_service.py``.
    2. Subclass ``Provider``, set ``name``, implement ``fetch()``.
    3. Decorate the class with ``@register``.
    4. Import it in ``providers/__init__.py``.

No other code needs to change. The CLI, config and fallback chain all
consume providers through the registry only.
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

USER_AGENT = "Mozilla/5.0 (compatible; ezwork-fetch/0.1)"

# Error categories — the fallback chain can treat these uniformly.
# "invalid" is NOT retriable on another provider; the rest are.
CATEGORY_TIMEOUT = "timeout"
CATEGORY_NETWORK = "network"
CATEGORY_HTTP = "http"
CATEGORY_EMPTY = "empty"
CATEGORY_INVALID = "invalid"


class FetchError(Exception):
    """A provider failed to fetch a URL. Carries a machine-readable category."""

    def __init__(self, message: str, category: str, http_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.category = category
        self.http_code = http_code

    def __str__(self) -> str:  # compact one-line form for stderr logs
        msg = self.args[0] if self.args else str(self)
        if self.http_code is not None:
            return f"{msg} (HTTP {self.http_code})"
        return msg



@dataclass
class FetchResult:
    """Successful fetch: markdown content plus metadata."""

    provider: str
    content: str
    url: str  # original requested URL
    elapsed: float  # seconds
    tokens: Optional[int] = None  # from x-markdown-tokens header, if present


@dataclass
class ProviderOpts:
    """Per-provider options resolved from config (keys are provider names)."""

    timeouts: dict = field(default_factory=dict)
    api_keys: dict = field(default_factory=dict)


class Provider:
    """Base class for a URL→Markdown service.

    Subclasses must set ``name`` (unique, lowercase identifier used in
    the config file) and implement ``fetch()``. Failures must
    be raised as ``FetchError``; unexpected exceptions are wrapped by
    ``fetch()`` into a network-category error automatically.
    """

    name: str = ""
    base_url: str = ""

    def __init__(self, opts: ProviderOpts | None = None) -> None:
        self.opts = opts or ProviderOpts()
        self.api_key = (self.opts.api_keys or {}).get(self.name)

    # -- overridable -----------------------------------------------------

    def build_headers(self) -> dict:
        return {"User-Agent": USER_AGENT, "Accept": "text/markdown"}

    def build_target(self, url: str) -> str:
        """Compose the service endpoint for a URL.

        The raw URL is appended to the base. Non-ASCII characters
        (Chinese, spaces, etc.) are percent-encoded automatically —
        HTTP request lines must be ASCII, and the services expect the
        same form a browser address bar shows. Existing %XX escapes are
        preserved, so already-encoded URLs pass through untouched.
        """
        return self.base_url + ensure_ascii(url).lstrip("/")

    def timeout(self, default: int) -> int:
        return int((self.opts.timeouts or {}).get(self.name, default))

    def parse_body(self, status: int, headers, body: bytes) -> str:
        """Turn the raw response body into markdown text.

        Default: decode the body as UTF-8. Override for services that
        wrap the markdown in JSON (e.g. Firecrawl's ``data.markdown``).
        """
        return body.decode("utf-8", errors="replace")

    def _request(self, target: str, timeout: int):
        """Issue the provider request; returns (status, headers, body bytes).

        Default is a plain GET. Override for POST/JSON APIs. Errors must
        be raised as ``FetchError`` (see ``_map_error``).
        """
        return self._http_get(target, timeout)

    # -- final -----------------------------------------------------------

    def fetch(self, url: str, timeout: int = 30) -> FetchResult:
        """Fetch ``url`` through this provider. Returns markdown text.

        Wraps all unexpected errors into ``FetchError`` (network category)
        so the fallback chain never crashes.
        """
        t0 = time.monotonic()
        try:
            status, headers, body = self._request(self.build_target(url), timeout)
        except FetchError as e:
            raise
        except Exception as e:  # defensive: providers must never crash the chain
            raise FetchError(f"{type(e).__name__}: {e}", CATEGORY_NETWORK) from e

        if status != 200:
            raise FetchError(f"{self.name} returned HTTP {status}", CATEGORY_HTTP, status)
        text = self.parse_body(status, headers, body).strip()
        if not text:
            raise FetchError(f"{self.name} returned empty content", CATEGORY_EMPTY)

        tokens = None
        raw = headers.get("x-markdown-tokens")
        if raw and raw.isdigit():
            tokens = int(raw)
        return FetchResult(
            provider=self.name, content=text, url=url,
            elapsed=round(time.monotonic() - t0, 3), tokens=tokens,
        )

    # -- internal http ---------------------------------------------------

    def _http_get(self, target: str, timeout: int):
        """GET ``target``; returns (status, headers, body bytes).

        Maps stdlib errors onto the FetchError taxonomy.
        """
        req = urllib.request.Request(target, headers=self.build_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except Exception as e:
            raise self._map_error(e, timeout) from e

    def _map_error(self, e: Exception, timeout: int) -> FetchError:
        """Map stdlib HTTP/network exceptions onto the FetchError taxonomy."""
        if isinstance(e, urllib.error.HTTPError):
            return FetchError(f"HTTP {e.code}", CATEGORY_HTTP, e.code)
        if isinstance(e, urllib.error.URLError):
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return FetchError(f"timed out after {timeout}s", CATEGORY_TIMEOUT)
            return FetchError(f"connection failed: {reason}", CATEGORY_NETWORK)
        if isinstance(e, (TimeoutError, socket.timeout)):
            return FetchError(f"timed out after {timeout}s", CATEGORY_TIMEOUT)
        return FetchError(f"{type(e).__name__}: {e}", CATEGORY_NETWORK)


def ensure_ascii(url: str) -> str:
    """Percent-encode non-ASCII characters in a URL.

    Pure-ASCII URLs are returned unchanged. Existing ``%XX`` escapes are
    preserved, so already-encoded URLs never get double-encoded. Callers
    can pass raw URLs (e.g. ``https://zh.wikipedia.org/wiki/人工智能``)
    and get a request-line-safe target.
    """
    try:
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass
    out = []
    i = 0
    n = len(url)
    while i < n:
        ch = url[i]
        if ch == "%" and i + 2 < n:
            hexpart = url[i + 1:i + 3]
            if all(c in "0123456789abcdefABCDEF" for c in hexpart):
                out.append(url[i:i + 3])  # keep existing escape
                i += 3
                continue
        if ord(ch) > 127:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Registry — the single extension point for new services.
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    """Class decorator: add a provider to the registry under its ``name``."""
    if not cls.name:
        raise ValueError(f"Provider {cls.__name__} must define a non-empty 'name'")
    PROVIDERS[cls.name] = cls
    return cls


def create_provider(name: str, opts: ProviderOpts | None = None) -> Provider:
    """Instantiate a registered provider by name."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS)) or "(none)"
        raise FetchError(
            f"unknown provider '{name}' (available: {known})", CATEGORY_INVALID
        ) from None
    return cls(opts)


def provider_names() -> list[str]:
    return sorted(PROVIDERS)
