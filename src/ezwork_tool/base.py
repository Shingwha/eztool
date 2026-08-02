"""统一数据结构和 Provider 基类：search / fetch / convert_file 三种能力。

服务商（provider）是唯一扩展点：子类声明 ``name`` + ``capabilities``，
实现对应能力方法，``@register`` 登记后即可被 CLI / 回退链 / 配置消费。
未实现的能力默认抛 CATEGORY_INVALID（chain 也会按 capabilities 预跳过）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import (  # re-export：provider 实现与测试从本模块拿全套
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    FetchError,
    ServiceError,
)
from .http import (  # re-export
    USER_AGENT,
    build_multipart,
    ensure_ascii,
    http_get,
)

__all__ = [
    "CATEGORY_EMPTY", "CATEGORY_HTTP", "CATEGORY_INVALID", "CATEGORY_NETWORK",
    "CATEGORY_TIMEOUT", "FetchError", "ServiceError",
    "USER_AGENT", "build_multipart", "ensure_ascii", "http_get",
    "FetchResult", "ProviderOpts", "SearchResult", "SearchResponse",
    "ParamSpec", "Provider",
]


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


@dataclass
class SearchResult:
    """单条搜索结果（三个搜索后端统一结构）。"""

    title: str
    url: str
    snippet: str = ""
    content: str | None = None  # 正文（anysearch 常带；doubao 需 need_content）
    extra: dict | None = None   # 后端特有元数据（如 deepseek 的 page_age）


@dataclass
class SearchResponse:
    """搜索响应（后端填充；api.search 回填 backend 名）。"""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    answer: str | None = None   # 仅 deepseek：AI 合成回答
    metadata: dict | None = None  # backend / total_results / search_time_ms / request_id


@dataclass
class ParamSpec:
    """search 特有参数的声明（CLI 自动生成 argparse 参数 + 归属校验）。

    type 仅 str/int；bool 用 action="store_true"（默认 None，传了为 True）。
    """

    help: str = ""
    type: type = str
    metavar: str | None = None
    choices: tuple | None = None
    action: str | None = None  # "store_true"


class Provider:
    """服务商基类。子类声明 name + capabilities，实现对应能力方法。"""

    name: str = ""
    capabilities: frozenset = frozenset()  # {"search"} / {"fetch"} / {"fetch", "convert_file"}
    search_params: dict[str, ParamSpec] = {}  # search 特有参数声明（CLI 生成）
    base_url: str = ""

    def __init__(self, opts: ProviderOpts | None = None) -> None:
        self.opts = opts or ProviderOpts()
        self.api_key = (self.opts.api_keys or {}).get(self.name)

    # -- search 能力（默认不支持）-------------------------------------------

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        """执行搜索。cfg 为统一配置（providers.<name> 段读凭证）。"""
        raise ServiceError(f"{self.name} does not support search", CATEGORY_INVALID)

    def has_credentials(self, cfg: dict) -> bool:
        """是否已配置凭证（config test / auto 路由预检查用）。默认看 api_key。"""
        return bool(self.api_key)

    def test_credentials(self, cfg: dict) -> str:
        """发最小请求验证凭证/连通性，返回描述字符串，失败抛异常。"""
        raise ServiceError(f"{self.name} has no credential check", CATEGORY_INVALID)

    # -- convert_file 能力（默认不支持）-------------------------------------

    def convert_file(self, path: str, timeout: int = 60) -> FetchResult:
        """本地文件 → Markdown（multipart 上传）。"""
        raise ServiceError(
            f"{self.name} does not support file conversion", CATEGORY_INVALID
        )

    # -- fetch 能力 ---------------------------------------------------------

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
        be raised as ``ServiceError`` (see http.map_http_error).
        """
        return http_get(target, self.build_headers(), timeout)

    def _http_get(self, target: str, timeout: int):
        """GET with the provider's own headers; (status, headers, body bytes)."""
        return http_get(target, self.build_headers(), timeout)

    def fetch(self, url: str, timeout: int = 30) -> FetchResult:
        """Fetch ``url`` through this provider. Returns markdown text.

        Wraps all unexpected errors into ``ServiceError`` (network category)
        so the fallback chain never crashes.
        """
        t0 = time.monotonic()
        try:
            status, headers, body = self._request(self.build_target(url), timeout)
        except ServiceError:
            raise
        except Exception as e:  # defensive: providers must never crash the chain
            raise ServiceError(f"{type(e).__name__}: {e}", CATEGORY_NETWORK) from e

        if status != 200:
            raise ServiceError(
                f"{self.name} returned HTTP {status}", CATEGORY_HTTP, http_code=status
            )
        text = self.parse_body(status, headers, body).strip()
        if not text:
            raise ServiceError(f"{self.name} returned empty content", CATEGORY_EMPTY)

        tokens = None
        raw = headers.get("x-markdown-tokens")
        if raw and raw.isdigit():
            tokens = int(raw)
        return FetchResult(
            provider=self.name, content=text, url=url,
            elapsed=round(time.monotonic() - t0, 3), tokens=tokens,
        )
