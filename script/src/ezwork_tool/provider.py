"""Provider 基类 + 注册表 + 元数据聚合（合并自 base/registry）。

每个 provider 文件 = 一个模块，内含：实现类 + 元数据声明（config 配置键 /
params CLI 参数面 / priority 默认链排序 / auth_required 凭证要求）。
注册点收敛在 ``providers/__init__.py`` 的显式 import 列表——没 import 的
模块不注册，新增 provider = 写一个文件 + 加一行 import，其余（config 键、
CLI 参数、默认链、config show）全部自动出现。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .util import (  # re-export：provider 实现与测试从本模块拿全套
    CATEGORY_BLOCKED,
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    ServiceError,
    USER_AGENT,
    build_multipart,
    checked_text,
    ensure_ascii,
    http_get,
)

__all__ = [
    "CATEGORY_BLOCKED", "CATEGORY_EMPTY", "CATEGORY_HTTP", "CATEGORY_INVALID",
    "CATEGORY_NETWORK", "CATEGORY_TIMEOUT", "ServiceError",
    "USER_AGENT", "build_multipart", "checked_text", "ensure_ascii", "http_get",
    "FetchResult", "ProviderOpts", "SearchResult", "SearchResponse",
    "ParamSpec", "Provider", "register", "SERVICES", "providers_for",
    "category_params", "default_chain", "provider_config_map",
]


@dataclass
class FetchResult:
    """Successful fetch: markdown content plus metadata."""

    provider: str
    content: str
    url: str  # original requested URL
    elapsed: float  # seconds
    tokens: Optional[int] = None  # from x-markdown-tokens header, if present
    low_quality: bool = False  # 内容可疑（命中拦截话术且偏短）：链应继续尝试更好结果
    quality_reason: str = ""    # 可疑原因（命中的拦截词等，供 log / 警告）


@dataclass
class ProviderOpts:
    """Per-provider options resolved from config (keys are provider names)."""

    timeouts: dict = field(default_factory=dict)
    api_keys: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """单条搜索结果（所有搜索后端统一结构）。"""

    title: str
    url: str
    snippet: str = ""
    content: str | None = None  # 正文（anysearch 常带；doubao 需 need_content）
    extra: dict | None = None   # 后端特有元数据（如 deepseek 的 page_age）
    source: str | None = None   # 命中的 provider 名（并行合并时回填）


@dataclass
class SearchResponse:
    """搜索响应（后端填充；api 层回填 backend 名）。"""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    answer: str | None = None   # 仅 deepseek：AI 合成回答
    metadata: dict | None = None  # backend / total_results / search_time_ms / request_id


@dataclass
class ParamSpec:
    """provider 特有参数的声明（CLI 自动生成 argparse 参数）。

    type 仅 str/int；bool 用 action="store_true"（默认 None，传了为 True）。
    """

    help: str = ""
    type: type = str
    metavar: str | None = None
    choices: tuple | None = None
    action: str | None = None  # "store_true"


class Provider:
    """服务商基类。子类声明元数据 + 实现对应能力方法。

    - ``categories``：支持哪些类别（search.web / search.image / search.data /
      convert.page / convert.file），回退链据此过滤。
    - ``params``：``{类别: {参数名: ParamSpec}}``，CLI 参数面自动并入。
    - ``config``：``{配置键: {default, secret, hint}}``（相对 providers.<name>
      段），自动生成 DEFAULTS / SECRET_KEYS / KEY_HINTS——加配置键只改这里。
    - ``priority``：``{类别: 排序值}``，默认回退链按此排序（小在前）。不声明
      的类别 = 不进默认链（如 exa），用户可 --providers 显式指定或配置链。
    - ``auth_required``：True = 必须有凭证才能用（默认链会跳过未配凭证的）；
      False = 匿名可用（限流），永远进链。
    """

    name: str = ""
    categories: frozenset = frozenset()
    params: dict[str, dict[str, ParamSpec]] = {}
    config: dict[str, dict[str, Any]] = {}
    priority: dict[str, int] = {}
    auth_required: bool = False
    base_url: str = ""

    def __init__(self, opts: ProviderOpts | None = None) -> None:
        self.opts = opts or ProviderOpts()
        self.api_key = (self.opts.api_keys or {}).get(self.name)

    # -- search 能力（默认不支持）-------------------------------------------

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        """执行搜索。cfg 为统一配置（providers.<name> 段读凭证）。"""
        raise ServiceError(f"{self.name} does not support search", CATEGORY_INVALID)

    def has_credentials(self, cfg: dict) -> bool:
        """是否已配置凭证（config test / 链凭证过滤用）。默认看 api_key。"""
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
        """Compose the service endpoint for a URL（非 ASCII 自动百分号编码）。"""
        return self.base_url + ensure_ascii(url).lstrip("/")

    def timeout(self, default: int) -> int:
        return int((self.opts.timeouts or {}).get(self.name, default))

    def parse_body(self, status: int, headers, body: bytes) -> str:
        """Turn the raw response body into markdown text（默认按 UTF-8 解码）。"""
        return body.decode("utf-8", errors="replace")

    def _request(self, target: str, timeout: int):
        """Issue the provider request; returns (status, headers, body bytes).

        Default is a plain GET. Override for POST/JSON APIs. Errors must
        be raised as ``ServiceError`` (see util.map_http_error).
        """
        return http_get(target, self.build_headers(), timeout)

    def _http_get(self, target: str, timeout: int):
        """GET with the provider's own headers; (status, headers, body bytes)."""
        return http_get(target, self.build_headers(), timeout)

    def fetch(self, url: str, timeout: int = 30) -> FetchResult:
        """Fetch ``url`` through this provider. Returns markdown text.

        Wraps all unexpected errors into ``ServiceError`` (network category)
        so the chain / parallel fan-out never crashes.
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

        # 质量门：拦截"假成功"（HTTP 200 + 非空但实为反爬/验证页）
        low_quality, reason = checked_text(self.name, text)

        tokens = None
        raw = headers.get("x-markdown-tokens")
        if raw and raw.isdigit():
            tokens = int(raw)
        return FetchResult(
            provider=self.name, content=text, url=url,
            elapsed=round(time.monotonic() - t0, 3), tokens=tokens,
            low_quality=low_quality, quality_reason=reason,
        )


# ── 注册表 ─────────────────────────────────────────────────────────────────

SERVICES: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    """类装饰器：登记服务商（重复 name 报错，防静默覆盖）。"""
    if not cls.name:
        raise ValueError(f"Provider {cls.__name__} must define a non-empty 'name'")
    if cls.name in SERVICES:
        raise ValueError(f"duplicate provider name: {cls.name}")
    SERVICES[cls.name] = cls
    return cls


def providers_for(category: str) -> list[str]:
    """该类别的全部候选 provider（注册顺序）。未知类别返回空列表。"""
    return [n for n, cls in SERVICES.items() if category in cls.categories]


def category_params(category: str) -> dict[str, ParamSpec]:
    """该类别全部参数：provider 特有参数并集（CLI 生成 argparse 用）。"""
    out: dict[str, ParamSpec] = {}
    for name in providers_for(category):
        for pname, spec in (SERVICES[name].params or {}).get(category, {}).items():
            if pname in out:
                raise ValueError(
                    f"param '{pname}' of '{name}' collides with another provider "
                    f"in category '{category}' (rename it)"
                )
            out[pname] = spec
    return out


def default_chain(category: str) -> list[str]:
    """该类别的默认回退链：按 priority 排序（未声明 priority 的 provider 不进链）。

    等价于旧 config.py 硬编码的 ``search.web.providers`` 等默认值——现在
    由 provider 声明自动派生，新增 provider 声明 priority 即自动进链。
    """
    ranked = []
    for name in providers_for(category):
        prio = (SERVICES[name].priority or {}).get(category)
        if prio is None:
            continue  # 不进默认链（如 exa），可 --providers 显式指定
        ranked.append((prio, name))
    return [name for _, name in sorted(ranked)]


def provider_config_map() -> dict[str, dict[str, dict[str, Any]]]:
    """全部 provider 的配置键声明聚合：``{provider: {key: {default, secret, hint}}}``。

    config.py 的 DEFAULTS / SECRET_KEYS / KEY_HINTS 全部由它生成——加配置键
    只改 provider 文件的 ``config`` 声明一处。
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name, cls in SERVICES.items():
        out[name] = {}
        for key, meta in (cls.config or {}).items():
            entry = dict(meta)
            entry.setdefault("default", None)
            entry.setdefault("secret", False)
            entry.setdefault("hint", "")
            out[name][key] = entry
    return out
