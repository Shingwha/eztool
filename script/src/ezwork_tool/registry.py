"""统一服务商注册表：search / fetch / convert 三类能力一个注册点。

行业惯例（插件式注册）：服务商类声明 ``name`` + ``capabilities`` +
``search_params``，``@register`` 登记；CLI 参数生成、回退链、凭证检查、
``--list-providers`` 全部只消费注册表——新增服务商不再改动任何公共代码。
"""

from __future__ import annotations

from .base import Provider, ProviderOpts
from .errors import CATEGORY_INVALID, ServiceError

SERVICES: dict[str, type[Provider]] = {}

# search 特有参数 → 归属服务商（注册时校验全局唯一）
_search_param_owners: dict[str, str] = {}


def register(cls: type[Provider]) -> type[Provider]:
    """类装饰器：把服务商登记到注册表。"""
    if not cls.name:
        raise ValueError(f"Provider {cls.__name__} must define a non-empty 'name'")
    if cls.name in SERVICES:
        raise ValueError(f"duplicate provider name: {cls.name}")
    for pname in cls.search_params:
        owner = _search_param_owners.get(pname)
        if owner is not None:
            raise ValueError(
                f"search param '{pname}' already owned by '{owner}' "
                f"(cannot also be declared by '{cls.name}')"
            )
        _search_param_owners[pname] = cls.name
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


def search_services() -> list[str]:
    """具备 search 能力的服务商（``eztool search --backend`` 候选）。"""
    return sorted(n for n, c in SERVICES.items() if "search" in c.capabilities)


def search_param_owners() -> dict[str, str]:
    """search 特有参数 → 归属服务商（参数归属校验用）。"""
    return dict(_search_param_owners)


def all_search_params() -> dict:
    """合并全部 search 服务商的特有参数声明（CLI 生成 argparse 用）。"""
    out: dict = {}
    for name in search_services():
        out.update(SERVICES[name].search_params)
    return out


def file_convert_services() -> list[str]:
    """具备 convert_file 能力的服务商（``eztool convert --list-providers``）。"""
    return sorted(n for n, c in SERVICES.items() if "convert_file" in c.capabilities)
