"""eztool 统一配置：~/.config/ezwork-tool/config.json，按后端分段（doubao/anysearch/deepseek/fetch）。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

CONFIG_DIR_ENV = "EZTOOL_CONFIG_DIR"

# 各段默认值（load 时与文件深合并，保证缺键可用）
DEFAULTS: dict[str, Any] = {
    "doubao": {
        "api_key": None, "ak": None, "sk": None, "auth": None,
        "count_web": 10, "count_image": 5, "need_url": False,
        "need_content": False, "content_formats": None,
        "time_range": None, "industry": None, "timeout": 30,
    },
    "anysearch": {"api_key": None, "max_results": 10},
    "deepseek": {"api_key": None, "model": "deepseek-v4-flash",
                 "thinking": "enabled", "max_tokens": 32768},
    "fetch": {
        "providers": ["firecrawl", "markdown", "jina"], "timeout": 30,
        "firecrawl": {"api_key": None, "timeout": 60},
        "markdown": {"timeout": 30},
        "jina": {"api_key": None, "timeout": 10},
    },
    "convert": {
        "providers": ["markdown", "mineru"], "timeout": 60,
        "markdown": {"timeout": 60},
        "mineru": {"timeout": 300},
    },
}

SECRET_KEYS = frozenset({
    "doubao.api_key", "doubao.ak", "doubao.sk",
    "anysearch.api_key", "deepseek.api_key",
    "fetch.firecrawl.api_key", "fetch.jina.api_key",
    "fetch.mineru.api_key", "convert.mineru.api_key",
})

# 每个可设键的说明（config set 提示 / --help 用）
KEY_HINTS = {
    "doubao.api_key": "豆包 WebSearch API Key（Bearer）",
    "doubao.ak": "火山引擎 AccessKey",
    "doubao.sk": "火山引擎 SecretKey",
    "doubao.auth": "鉴权方式：apikey / aksk（留空自动检测）",
    "doubao.count_web": "网页结果数（1-50）",
    "doubao.count_image": "图片结果数（1-5）",
    "doubao.need_url": "只返回带落地链接的结果（true/false）",
    "doubao.need_content": "只返回带正文的结果（true/false）",
    "doubao.content_formats": "正文格式：text / markdown",
    "doubao.time_range": "时间范围：OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD",
    "doubao.industry": "行业搜索：finance / game / gov",
    "doubao.timeout": "doubao 请求超时秒数",
    "anysearch.api_key": "AnySearch API Key（可选，匿名可用）",
    "anysearch.max_results": "结果数（1-20）",
    "deepseek.api_key": "DeepSeek API Key（https://platform.deepseek.com）",
    "deepseek.model": "模型：deepseek-v4-flash / deepseek-v4-pro",
    "deepseek.thinking": "思考模式：enabled / disabled（enabled 更准但更慢更贵）",
    "deepseek.max_tokens": "最大输出 token 数",
    "fetch.providers": "抓取回退链，逗号分隔：firecrawl,markdown,jina",
    "fetch.timeout": "抓取默认超时秒数",
    "fetch.firecrawl.api_key": "Firecrawl API Key（可选）",
    "fetch.firecrawl.timeout": "firecrawl 超时秒数",
    "fetch.markdown.timeout": "markdown.new 超时秒数",
    "fetch.jina.api_key": "Jina API Key（可选）",
    "fetch.jina.timeout": "jina 超时秒数",
    "convert.providers": "文件转 Markdown 回退链，逗号分隔：markdown,mineru",
    "convert.timeout": "文件转换默认超时秒数",
    "convert.markdown.timeout": "markdown.new 文件转换超时秒数",
    "convert.mineru.timeout": "MinerU 提取任务总超时秒数（异步提交+轮询，默认 300）",
    "fetch.mineru.timeout": "MinerU URL 提取任务总超时秒数（默认 300）",
    "convert.mineru.api_key": "MinerU Token（可选）：配了走 v4 Precision API（≤200MB/200页/批量/HTML）；不配走 v1 轻量 API（≤10MB/20页）",
    "fetch.mineru.api_key": "MinerU Token（可选），与 convert.mineru.api_key 二选一配置即可，自动互通",
}


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
