"""eztool 统一配置：~/.config/ezwork-tool/config.json。

两层结构：``providers.<name>`` 段放各服务商凭证/超时；``search.<类别>`` /
``convert.<类别>`` 段（search.web / search.image / search.data /
convert.page / convert.file）放类别回退链与缺省超时。
类别段 ``providers`` 缺省 = 该类别的注册顺序（registry），显式配置则覆盖。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

CONFIG_DIR_ENV = "EZTOOL_CONFIG_DIR"

# 各段默认值（load 时与文件深合并，保证缺键可用）
DEFAULTS: dict[str, Any] = {
    "providers": {
        "doubao": {
            "api_key": None, "ak": None, "sk": None, "auth": None,
            "count_web": 20, "count_image": 5, "need_url": False,
            "need_content": False, "content_formats": None,
            "time_range": None, "industry": None, "timeout": 30,
        },
        "anysearch": {"api_key": None, "max_results": 20},
        "deepseek": {"api_key": None, "model": "deepseek-v4-flash",
                     "thinking": "enabled", "max_tokens": 32768},
        "firecrawl": {"api_key": None, "timeout": 60},
        "markdown_new": {"timeout": 30},
        "jina_reader": {"api_key": None, "timeout": 10},
        "mineru": {"api_key": None, "timeout": 300},
        "anydoc": {"timeout": 60},
        "tavily": {"api_key": None, "timeout": 30},
        "exa": {"api_key": None, "timeout": 30},
    },
    # 搜索类别段：回退链默认顺序（与 registry 注册顺序同源，可显式覆盖）
    "search": {
        "web": {"providers": ["doubao", "anysearch", "deepseek"], "timeout": 30},
        "image": {"providers": ["doubao"], "timeout": 30},
        "data": {"providers": ["anysearch"], "timeout": 30},
    },
    # 转换类别段：page=URL 抓取（免费优先，tavily 兜底反爬站），file=本地文件（本地解析优先）
    "convert": {
        "page": {"providers": ["markdown_new", "jina_reader", "anysearch", "tavily", "firecrawl"], "timeout": 30},
        "file": {"providers": ["anydoc", "markdown_new", "mineru"], "timeout": 60},
    },
}

SECRET_KEYS = frozenset({
    "providers.doubao.api_key", "providers.doubao.ak", "providers.doubao.sk",
    "providers.anysearch.api_key", "providers.deepseek.api_key",
    "providers.firecrawl.api_key", "providers.jina_reader.api_key",
    "providers.mineru.api_key",
    "providers.tavily.api_key", "providers.exa.api_key",
})

# 每个可设键的说明（config set 提示 / --help 用）
KEY_HINTS = {
    "providers.doubao.api_key": "豆包 WebSearch API Key（Bearer）",
    "providers.doubao.ak": "火山引擎 AccessKey",
    "providers.doubao.sk": "火山引擎 SecretKey",
    "providers.doubao.auth": "鉴权方式：apikey / aksk（留空自动检测）",
    "providers.doubao.count_web": "网页结果数（1-50）",
    "providers.doubao.count_image": "图片结果数（1-5）",
    "providers.doubao.need_url": "只返回带落地链接的结果（true/false）",
    "providers.doubao.need_content": "只返回带正文的结果（true/false）",
    "providers.doubao.content_formats": "正文格式：text / markdown",
    "providers.doubao.time_range": "时间范围：OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD",
    "providers.doubao.industry": "行业搜索：finance / game / gov",
    "providers.doubao.timeout": "doubao 请求超时秒数",
    "providers.anysearch.api_key": "AnySearch API Key（可选，匿名可用）",
    "providers.anysearch.max_results": "结果数（1-20）",
    "providers.deepseek.api_key": "DeepSeek API Key（https://platform.deepseek.com）",
    "providers.deepseek.model": "模型：deepseek-v4-flash / deepseek-v4-pro",
    "providers.deepseek.thinking": "思考模式：enabled / disabled（enabled 更准但更慢更贵）",
    "providers.deepseek.max_tokens": "最大输出 token 数",
    "providers.firecrawl.api_key": "Firecrawl API Key（可选）",
    "providers.firecrawl.timeout": "firecrawl 超时秒数",
    "providers.markdown_new.timeout": "markdown.new 超时秒数",
    "providers.jina_reader.api_key": "Jina API Key（可选）",
    "providers.jina_reader.timeout": "jina 超时秒数",
    "providers.mineru.api_key": "MinerU Token（可选）：配了走 v4 Precision API（≤200MB/200页/批量/HTML）；不配走 v1 轻量 API（≤10MB/20页）",
    "providers.mineru.timeout": "MinerU 提取任务总超时秒数（异步提交+轮询，默认 300）",
    "providers.tavily.api_key": "Tavily API Key（不配则自动走 keyless 免费模式）",
    "providers.tavily.timeout": "tavily 请求超时秒数",
    "providers.exa.api_key": "Exa API Key（https://dashboard.exa.ai）",
    "providers.exa.timeout": "exa 请求超时秒数",
    "providers.anydoc.timeout": "anydoc 本地解析超时秒数",
    "search.web.providers": "网页搜索回退链，逗号分隔：doubao,anysearch,deepseek",
    "search.web.timeout": "网页搜索默认超时秒数",
    "search.image.providers": "图片搜索回退链，逗号分隔：doubao",
    "search.image.timeout": "图片搜索默认超时秒数",
    "search.data.providers": "专业数据源回退链，逗号分隔：anysearch",
    "search.data.timeout": "专业数据源搜索默认超时秒数",
    "convert.page.providers": "URL → Markdown 抓取链，逗号分隔（免费优先，tavily 兜底反爬站）：markdown_new,jina_reader,anysearch,tavily,firecrawl",
    "convert.page.timeout": "URL 抓取默认超时秒数",
    "convert.file.providers": "文件 → Markdown 转换链，逗号分隔（本地解析优先）：anydoc,markdown_new,mineru",
    "convert.file.timeout": "文件转换默认超时秒数",
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
