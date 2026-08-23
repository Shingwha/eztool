"""DeepSeek 服务端搜索后端：一次请求完成 搜索 → 抓取 → 服务端解密 → AI 合成回答。

移植自 deepseek-websearch 的 core.py（Anthropic 兼容端点 + web_search_20250305
服务端工具，纯标准库 urllib）。只移植核心逻辑；CLI / 配置管理 / formatter
由 eztool 公共层统一，不在此处。

数据结构：AI 回答放 response.answer，来源列表放 response.results。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Literal

from ..provider import (
    CATEGORY_HTTP,
    Provider,
    SearchResponse,
    SearchResult,
    ServiceError,
    post_json,
    register,
)
from ..util import CredentialsError, NoResultsError

# ── 常量 ───────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 32768
DEFAULT_TIMEOUT = 120

# ── System prompt（逐字保留自原 core.py / core.ts）────────────────────────────

SEARCH_SYSTEM_PROMPT = """You are a web search assistant. Follow these rules strictly:

1. Use web_search to find relevant, up-to-date information for the user's query.
2. After receiving search results, write a comprehensive, well-structured answer
   in plain text based on what you found. Include specific details, dates, and facts.
3. Do NOT output tool-call XML (no <invoke> tags).
4. Do NOT call web_search again after you have results.
5. Answer in the same language the user used in their query.
6. If search results are poor or irrelevant, explain why and suggest better keywords.

Your response must be the final answer, not another search request."""


def _base_url() -> str:
    """基址可用环境变量 DEEPSEEK_WS_BASE_URL 覆盖（全项目唯一保留的 env）。"""
    return os.environ.get("DEEPSEEK_WS_BASE_URL") or DEFAULT_BASE_URL


# ── 核心搜索 ─────────────────────────────────────────────────────────────────


def _request_search(
    *,
    query: str,
    api_key: str,
    model: str,
    thinking: Literal["enabled", "disabled"],
    max_tokens: int,
    base_url: str,
    timeout: int,
) -> tuple[str, list[SearchResult]]:
    """调 Anthropic 兼容端点（移植自原 core.py 的 search_web）。

    返回 (AI 回答, 来源列表)。API/网络错误抛 ServiceError，错误码沿用
    原仓库（api_error / network_error）。
    """
    url = f"{base_url}/v1/messages"

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "tool_choice": {"type": "auto"},
    }
    # 思考模式开启时加入配置
    if thinking == "enabled":
        body["thinking"] = {"type": "enabled"}

    # ── 发请求（传输层走 post_json，HTTP/网络错误已映射为 ServiceError）──
    try:
        _status, _headers, raw = post_json(url, {"x-api-key": api_key}, body, timeout)
    except ServiceError as e:
        # 错误码沿用原仓库：HTTP 层 api_error，网络/超时 network_error
        code = "api_error" if e.category == CATEGORY_HTTP else "network_error"
        raise ServiceError(
            f"DeepSeek API error: {e.message}",
            e.category, http_code=e.http_code, code=code,
        ) from None

    # ── 解析响应 ──
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(
            f"could not parse API response: {e}",
            CATEGORY_HTTP, code="api_error",
        ) from None

    content = data.get("content")
    if not isinstance(content, list):
        return "", []

    # 分别收集：
    #   - web_search_tool_result block → 单条结果（title/url，page_age 放 extra）
    #   - text block → 模型基于解密内容生成的最终回答
    results: list[SearchResult] = []
    text_parts: list[str] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "web_search_tool_result":
            inner = block.get("content")
            if not isinstance(inner, list):
                continue
            for item in inner:
                if isinstance(item, dict) and item.get("type") == "web_search_result":
                    extra = None
                    page_age = item.get("page_age")
                    if page_age is not None:
                        extra = {"page_age": page_age}
                    results.append(
                        SearchResult(
                            title=str(item.get("title") or "Untitled"),
                            url=str(item.get("url") or ""),
                            extra=extra,
                        )
                    )
        elif block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

    return "\n\n".join(text_parts), results


@register
class DeepSeekProvider(Provider):
    """deepseek 搜索后端（服务端搜索 + AI 合成回答）。"""

    name = "deepseek"
    categories = frozenset({"web"})
    config = {
        "api_key": {"secret": True, "hint": "DeepSeek API key (https://platform.deepseek.com)"},
        "model": {"default": "deepseek-v4-flash",
                  "hint": "model: deepseek-v4-flash / deepseek-v4-pro"},
        "thinking": {"default": "enabled",
                     "hint": "thinking mode: enabled / disabled "
                             "(enabled is more accurate but slower and costlier)"},
        "max_tokens": {"default": 32768, "hint": "max output tokens"},
    }
    priority = {"web": 30}
    auth_required = True
    # deepseek 无特有 CLI 参数（count/full 由服务端决定，忽略）

    def _require_api_key(self) -> str:
        """取 api_key，缺失抛 CredentialsError。"""
        if not self.api_key:
            raise CredentialsError(
                "DeepSeek API key not configured; "
                "run `eztool config set providers.deepseek.api_key <key>`",
                code="missing_api_key",
            )
        return self.api_key

    def test_credentials(self) -> str:
        """发最小请求验证密钥（移植自原 cli.py 的 cmd_config_test）。

        小 token（64）+ 关闭 thinking 加速。成功返回描述字符串，失败抛异常。
        """
        answer, results = _request_search(
            query="ping",
            api_key=self._require_api_key(),
            model=self.cfg.get("model") or DEFAULT_MODEL,
            thinking="disabled",
            max_tokens=64,
            base_url=_base_url(),
            timeout=30,
        )
        return (
            f"DeepSeek key valid, returned {len(results)} sources"
            + (f", answer {len(answer)} chars" if answer else "")
        )

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        """执行 DeepSeek 服务端搜索。

        self.cfg：api_key（必须）/ model（默认 deepseek-v4-flash）/
        thinking（enabled/disabled）/ max_tokens（默认 32768）。
        count/full 由服务端决定，opts 忽略。
        """
        api_key = self._require_api_key()
        model = self.cfg.get("model") or DEFAULT_MODEL
        thinking: Literal["enabled", "disabled"] = (
            "enabled" if (self.cfg.get("thinking") or "enabled") != "disabled" else "disabled"
        )
        max_tokens = int(self.cfg.get("max_tokens") or DEFAULT_MAX_TOKENS)
        timeout = self.timeout(DEFAULT_TIMEOUT)

        if not query or not query.strip():
            raise NoResultsError("search query is empty.")

        t0 = time.monotonic()
        answer, results = _request_search(
            query=query,
            api_key=api_key,
            model=model,
            thinking=thinking,
            max_tokens=max_tokens,
            base_url=_base_url(),
            timeout=timeout,
        )
        search_time_ms = int((time.monotonic() - t0) * 1000)

        # 空结果：无 AI 回答且无来源
        if not answer and not results:
            raise NoResultsError("no results found")

        return SearchResponse(
            query=query,
            results=results,
            answer=answer or None,
            metadata={"count": len(results), "search_time_ms": search_time_ms},
        )
