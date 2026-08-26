"""eztool 统一配置：~/.config/eztool/config.json。

四段式结构：

- ``settings.*``：全局设置（目前只有 ``timeout`` 默认超时，预留扩展）。
- ``chains.<类别>``：五条回退链（web/image/data/page/file），默认值由
  provider 的 ``priority`` 声明自动派生。
- ``providers.<name>.*``：各服务商凭证/私有配置，键由 provider 的
  ``config`` 声明自动生成。
- ``summarize.*``：--summarize 的 LLM 端点（OpenAI 兼容），显式声明
  （非 provider，不走元数据生成）。

providers 段的 DEFAULTS / SECRET_KEYS / KEY_HINTS 全部由
``provider.provider_config_map()`` + ``default_chain()`` 生成，新增
provider/配置键无需改这里。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from . import provider as prov
from . import providers as _providers  # noqa: F401  (side-effect: 先注册再构建 DEFAULTS)

CONFIG_DIR_ENV = "EZTOOL_CONFIG_DIR"

ALL_CATEGORIES = prov.SEARCH_CATEGORIES + prov.CONVERT_CATEGORIES


def _build_defaults() -> dict[str, Any]:
    """默认配置：settings + chains（自动派生）+ providers（元数据生成）。"""
    defaults: dict[str, Any] = {
        "settings": {"timeout": 30},
        "chains": {},
        "providers": {},
    }
    for cat in ALL_CATEGORIES:
        defaults["chains"][cat] = prov.default_chain(cat)  # 出厂默认 = 自动派生；可显式覆盖
    for name, keys in prov.provider_config_map().items():
        sec = {"timeout": 30}
        for key, meta in keys.items():
            sec[key] = meta["default"]
        defaults["providers"][name] = sec
    defaults["summarize"] = {
        "backend": "openai",       # 注册表选择器（拓展口）
        "base_url": None,          # 必需：OpenAI 兼容端点（如 https://api.deepseek.com）
        "api_key": None,           # 必需（secret）
        "model": None,             # 必需（如 deepseek-v4-flash）
        "timeout": 120,
    }
    return defaults


DEFAULTS = _build_defaults()


def _build_secret_keys() -> frozenset:
    keys: set[str] = {"summarize.api_key"}
    for name, kmap in prov.provider_config_map().items():
        for key, meta in kmap.items():
            if meta["secret"]:
                keys.add(f"providers.{name}.{key}")
    return frozenset(keys)


SECRET_KEYS = _build_secret_keys()


def _build_key_hints() -> dict[str, str]:
    hints: dict[str, str] = {"settings.timeout": "global default timeout in seconds"}
    hints["summarize.base_url"] = "OpenAI-compatible endpoint for --summarize (e.g. https://api.deepseek.com)"
    hints["summarize.api_key"] = "API key for the summarize endpoint"
    hints["summarize.model"] = "model name for --summarize (e.g. deepseek-v4-flash)"
    for cat in ALL_CATEGORIES:
        chain = ", ".join(prov.default_chain(cat)) or "(empty — no provider declares priority)"
        hints[f"chains.{cat}"] = f"{cat} fallback chain, comma-separated (default: {chain})"
    for name, kmap in prov.provider_config_map().items():
        for key, meta in kmap.items():
            if meta["hint"]:
                hints[f"providers.{name}.{key}"] = meta["hint"]
    return hints


KEY_HINTS = _build_key_hints()


def config_dir() -> str:
    return os.environ.get(CONFIG_DIR_ENV) or os.path.join(
        os.path.expanduser("~"), ".config", "eztool")


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


def load_overrides() -> dict:
    """用户显式设置的覆盖值 = 配置文件原始内容（稀疏；损坏/缺失返回空 dict）。

    ``config set/reset`` 在这份稀疏数据上增删并整体写回——文件里永远只有
    用户真正设置过的键，其余一律回落 ``DEFAULTS``。
    """
    path = config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass  # 损坏视同未配置（load_config 同样静默回退默认）
    return {}


def load_config() -> dict:
    """默认值 + 配置文件覆盖（损坏则静默回退默认）。"""
    return deep_merge(DEFAULTS, load_overrides())


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
    """把命令行字符串转成目标类型（chains.* → list；bool/int/str 自动识别）。"""
    if path.startswith("chains."):
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
