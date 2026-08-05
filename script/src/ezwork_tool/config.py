"""eztool 统一配置：~/.config/ezwork-tool/config.json。

结构：``providers.<name>`` 段（各服务商凭证/超时，键由 provider 的 ``config``
声明自动生成）+ ``search.<类别>`` / ``convert.<类别>`` 段（类别回退链与缺省
超时，链默认值由 provider 的 ``priority`` 自动派生）。

本模块只剩通用读写工具——DEFAULTS / SECRET_KEYS / KEY_HINTS 全部由
``provider.provider_config_map()`` 生成，新增 provider/配置键无需改这里。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from . import provider as prov
from . import providers as _providers  # noqa: F401  (side-effect: 先注册再构建 DEFAULTS)

CONFIG_DIR_ENV = "EZTOOL_CONFIG_DIR"

# 类别段结构（链默认值运行时从 provider.priority 派生）
_CATEGORY_SECTIONS = {
    "search": {"web", "image", "data"},
    "convert": {"page", "file"},
}


def _build_defaults() -> dict[str, Any]:
    """默认配置：providers 段来自元数据；类别段链默认值自动派生。"""
    defaults: dict[str, Any] = {"providers": {}, "search": {}, "convert": {}}
    for name, keys in prov.provider_config_map().items():
        sec = {"timeout": 30}
        for key, meta in keys.items():
            sec[key] = meta["default"]
        defaults["providers"][name] = sec
    for domain, ops in _CATEGORY_SECTIONS.items():
        for op in ops:
            cat = f"{domain}.{op}"
            defaults[domain][op] = {
                "providers": prov.default_chain(cat),  # 出厂默认 = 自动派生；可显式覆盖
                "timeout": 30,
            }
    return defaults


DEFAULTS = _build_defaults()


def _build_secret_keys() -> frozenset:
    keys: set[str] = set()
    for name, kmap in prov.provider_config_map().items():
        for key, meta in kmap.items():
            if meta["secret"]:
                keys.add(f"providers.{name}.{key}")
    return frozenset(keys)


SECRET_KEYS = _build_secret_keys()


def _build_key_hints() -> dict[str, str]:
    hints: dict[str, str] = {}
    for name, kmap in prov.provider_config_map().items():
        for key, meta in kmap.items():
            if meta["hint"]:
                hints[f"providers.{name}.{key}"] = meta["hint"]
    for domain, ops in _CATEGORY_SECTIONS.items():
        for op in ops:
            cat = f"{domain}.{op}"
            chain = ", ".join(prov.default_chain(cat)) or "(空——无 provider 声明 priority)"
            hints[f"{domain}.{op}.providers"] = (
                f"{domain}.{op} 回退链，逗号分隔（默认: {chain}）"
            )
            hints[f"{domain}.{op}.timeout"] = f"{domain}.{op} 默认超时秒数"
    return hints


KEY_HINTS = _build_key_hints()


def config_dir() -> str:
    return os.environ.get(CONFIG_DIR_ENV) or os.path.join(
        os.path.expanduser("~"), ".config", "ezwork-tool")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def deep_merge(base: dict, override: dict) -> dict:
    """深合并：返回全新 dict，不修改 base（嵌套结构与 list 全部复制）。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """默认值 + 配置文件（损坏则静默回退默认）。"""
    cfg = deep_merge(DEFAULTS, {})
    path = config_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = deep_merge(cfg, data)
        except (OSError, ValueError):
            pass  # 损坏回退默认
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_key(cfg: dict, path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_key(cfg: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def parse_value(path: str, raw: str) -> Any:
    """把命令行字符串转成目标类型（bool/int/list/str）。"""
    if path.endswith(".providers"):
        return [p.strip() for p in raw.split(",") if p.strip()]
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def mask_key(path: str, value: Any) -> str:
    if path not in SECRET_KEYS or not value:
        return str(value if value is not None else "")
    s = str(value)
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * 4 + s[-4:]
