"""统一服务商注册表：类别（category）即路由单元，一个注册点。

类别是路由与参数归属的最小单元，命名 ``<域>.<操作>``（search.web /
search.image / search.paper / search.data / convert.page / convert.file）。
服务商类声明 ``name`` + ``categories`` + ``category_params``，``@register``
登记；CLI 子命令生成、回退链过滤、参数面、凭证检查、``--list-providers``
全部只消费本注册表——新增类别 / 新增 provider 不再改动任何公共代码。
"""

from __future__ import annotations

import re

from .base import ParamSpec, Provider, ProviderOpts
from .errors import CATEGORY_INVALID, ServiceError

SERVICES: dict[str, type[Provider]] = {}

# 类别 → 支持该类别的 provider 名（按注册顺序；回退链候选与默认配置同源）
CATEGORIES: dict[str, list[str]] = {}

# 类别级公共参数：多个 provider 语义一致的通用参数（如域名定向），
# 提升为类别公共参数后无归属 provider——任何该类别 provider 都可从 opts 读取。
# 类似全局 --count/--timeout，但限定类别。provider 不得再声明同名参数。
PUBLIC_PARAMS: dict[str, dict[str, ParamSpec]] = {
    "search.web": {
        "include_domains": ParamSpec(
            metavar="DOMAINS", help="只搜这些域名，逗号分隔，如 mp.weixin.qq.com"
        ),
        "exclude_domains": ParamSpec(metavar="DOMAINS", help="排除域名，逗号分隔"),
    },
}

# CLI 内部字段（subparser dest / set_defaults / 硬编码参数）：
# provider 参数名与之冲突会覆盖路由与分派（如 --category 篡改路由类别、
# --func 劫持命令分派），注册期必须拒绝。
RESERVED_PARAM_NAMES = frozenset({
    "command", "search_cmd", "config_cmd",   # subparser dest
    "func", "category",                      # set_defaults 注入（默认值非 None，最危险）
    "query", "providers", "count", "timeout",
    "year", "author", "sort", "oa",
    "target", "out", "list_providers",
    "key", "value", "version",
})

_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _validate_category(cat: str, cls_name: str) -> None:
    if not _CATEGORY_RE.match(cat):
        raise ValueError(
            f"Provider {cls_name}: invalid category '{cat}' "
            f"(must be '<domain>.<operation>', e.g. 'search.web')"
        )


def register(cls: type[Provider]) -> type[Provider]:
    """类装饰器：把服务商登记到注册表（校验 name / 类别 / 参数归属）。"""
    if not cls.name:
        raise ValueError(f"Provider {cls.__name__} must define a non-empty 'name'")
    if cls.name in SERVICES:
        raise ValueError(f"duplicate provider name: {cls.name}")

    for cat in cls.categories:
        _validate_category(cat, cls.__name__)
        for pname in (cls.category_params or {}).get(cat, {}):
            if pname in RESERVED_PARAM_NAMES:
                raise ValueError(
                    f"category param '{pname}' of '{cls.name}' collides with a "
                    f"reserved CLI field (would corrupt routing/dispatch); "
                    f"rename the param (e.g. '--{pname}-x')"
                )
            if pname in PUBLIC_PARAMS.get(cat, {}):
                raise ValueError(
                    f"category param '{pname}' of '{cls.name}' collides with "
                    f"a public param of category '{cat}' (declare it once in PUBLIC_PARAMS)"
                )
            for other in CATEGORIES.get(cat, []):
                if pname in (SERVICES[other].category_params or {}).get(cat, {}):
                    raise ValueError(
                        f"category param '{pname}' of '{cls.name}' already "
                        f"declared by '{other}' for category '{cat}'"
                    )
        CATEGORIES.setdefault(cat, []).append(cls.name)

    SERVICES[cls.name] = cls
    return cls


def create_service(name: str, opts: ProviderOpts | None = None) -> Provider:
    """按名字实例化注册的服务商。"""
    try:
        cls = SERVICES[name]
    except KeyError:
        known = ", ".join(sorted(SERVICES)) or "(none)"
        raise ServiceError(
            f"unknown provider '{name}' (available: {known})", CATEGORY_INVALID
        ) from None
    return cls(opts)


def service_names() -> list[str]:
    return sorted(SERVICES)


def providers_for(category: str) -> list[str]:
    """该类别的回退链候选（注册顺序）。未知类别 → ServiceError。"""
    try:
        return list(CATEGORIES[category])
    except KeyError:
        known = ", ".join(sorted(CATEGORIES)) or "(none)"
        raise ServiceError(
            f"unknown category '{category}' (available: {known})", CATEGORY_INVALID
        ) from None


def category_params(category: str) -> dict[str, ParamSpec]:
    """该类别全部参数：provider 特有参数并集 + 类别公共参数（CLI 生成 argparse 用）。"""
    out: dict[str, ParamSpec] = {}
    for name in providers_for(category):
        out.update((SERVICES[name].category_params or {}).get(category, {}))
    out.update(PUBLIC_PARAMS.get(category, {}))
    return out


def search_categories() -> list[str]:
    """全部 search.* 类别（排序）→ CLI 生成 search 子命令。"""
    return sorted(c for c in CATEGORIES if c.startswith("search."))


def convert_page_services() -> list[str]:
    """convert.page（URL → Markdown）候选。"""
    return list(CATEGORIES.get("convert.page", []))


def convert_file_services() -> list[str]:
    """convert.file（本地文件 → Markdown）候选。"""
    return list(CATEGORIES.get("convert.file", []))


def category_of_name(name: str) -> str | None:
    """provider 归属的类别（--list-providers 展示用）；未注册返回 None。"""
    for cat, names in CATEGORIES.items():
        if name in names:
            return cat
    return None
