"""搜索后端统一数据结构。三个后端（doubao/anysearch/deepseek）都返回 SearchResponse。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    content: str | None = None  # 正文（anysearch 常带；doubao 需 need_content）
    extra: dict | None = None   # 后端特有元数据（如 deepseek 的 page_age）


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    answer: str | None = None   # 仅 deepseek：AI 合成回答
    metadata: dict | None = None  # backend / total_results / search_time_ms / request_id
