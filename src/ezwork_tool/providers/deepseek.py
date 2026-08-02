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
import urllib.error
import urllib.request
from typing import Any, Literal

from ..base import ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import BackendError, CredentialsError, NoResultsError
from ..registry import register

# ── 常量 ───────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 32768
DEFAULT_TIMEOUT = 120.0

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


# ── 凭证 ───────────────────────────────────────────────────────────────────


def _has_credentials(cfg: dict) -> bool:
    """deepseek 段配置了 api_key 即 True。"""
    return bool((cfg.get("providers", {}).get("deepseek") or {}).get("api_key"))


def _require_api_key(cfg: dict) -> str:
    """取 api_key，缺失抛 CredentialsError。"""
    api_key = (cfg.get("providers", {}).get("deepseek") or {}).get("api_key")
    if not api_key:
        raise CredentialsError(
            "未配置 DeepSeek API Key，请运行 `eztool config set deepseek.api_key <key>`",
            code="missing_api_key",
        )
    return api_key


def _test_credentials(cfg: dict) -> str:
    """发最小请求验证密钥（移植自原 cli.py 的 cmd_config_test）。

    小 token（64）+ 关闭 thinking 加速。成功返回描述字符串，失败抛异常。
    """
    api_key = _require_api_key(cfg)
    ds = cfg.get("providers", {}).get("deepseek") or {}
    base_url = os.environ.get("DEEPSEEK_WS_BASE_URL") or DEFAULT_BASE_URL
    answer, results = _request_search(
        query="ping",
        api_key=api_key,
        model=ds.get("model") or DEFAULT_MODEL,
        thinking="disabled",
        max_tokens=64,
        base_url=base_url,
        timeout=30.0,
    )
    return (
        f"DeepSeek 密钥有效，返回 {len(results)} 条来源"
        + (f"，回答 {len(answer)} 字符" if answer else "")
    )


# ── 核心搜索 ─────────────────────────────────────────────────────────────────


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 DeepSeek 服务端搜索。

    cfg["deepseek"]：api_key（必须）/ model（默认 deepseek-v4-flash）/
    thinking（enabled/disabled）/ max_tokens（默认 32768）。
    opts：仅用 timeout（int，覆盖默认 120）；count/full 由服务端决定，忽略。
    基址可用环境变量 DEEPSEEK_WS_BASE_URL 覆盖。
    """
    api_key = _require_api_key(cfg)
    ds = cfg.get("providers", {}).get("deepseek") or {}
    model = ds.get("model") or DEFAULT_MODEL
    thinking: Literal["enabled", "disabled"] = (
        "enabled" if (ds.get("thinking") or "enabled") != "disabled" else "disabled"
    )
    max_tokens = int(ds.get("max_tokens") or DEFAULT_MAX_TOKENS)
    timeout = float(int(opts.get("timeout") or DEFAULT_TIMEOUT))
    base_url = os.environ.get("DEEPSEEK_WS_BASE_URL") or DEFAULT_BASE_URL

    if not query or not query.strip():
        raise NoResultsError("搜索关键词为空。")

    t0 = time.monotonic()
    answer, results = _request_search(
        query=query,
        api_key=api_key,
        model=model,
        thinking=thinking,
        max_tokens=max_tokens,
        base_url=base_url,
        timeout=timeout,
    )
    search_time_ms = int((time.monotonic() - t0) * 1000)

    # 空结果：无 AI 回答且无来源
    if not answer and not results:
        raise NoResultsError("未找到结果")

    return SearchResponse(
        query=query,
        results=results,
        answer=answer or None,
        metadata={"count": len(results), "search_time_ms": search_time_ms},
    )


def _request_search(
    *,
    query: str,
    api_key: str,
    model: str,
    thinking: Literal["enabled", "disabled"],
    max_tokens: int,
    base_url: str,
    timeout: float,
) -> tuple[str, list[SearchResult]]:
    """调 Anthropic 兼容端点（移植自原 core.py 的 search_web）。

    返回 (AI 回答, 来源列表)。API/网络错误抛 BackendError，错误码沿用
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

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — 固定 https 端点，非用户可控 URL
        url,
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
        },
    )

    # ── 发请求 ──
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # API 返回非 2xx
        try:
            err_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_text = "Unable to read error body"
        raise BackendError(
            f"DeepSeek API error ({e.code}): {err_text}",
            code="api_error",
        ) from None
    except urllib.error.URLError as e:
        # 网络层错误（DNS、连接拒绝等）
        raise BackendError(
            f"Network error: {e.reason}",
            code="network_error",
        ) from None
    except TimeoutError:
        raise BackendError("Request timed out.", code="network_error") from None

    # ── 解析响应 ──
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BackendError(
            f"无法解析 API 响应：{e}",
            code="api_error",
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
    capabilities = frozenset({"search"})
    # deepseek 无特有 CLI 参数（count/full 由服务端决定，忽略）

    def has_credentials(self, cfg: dict) -> bool:
        return _has_credentials(cfg)

    def test_credentials(self, cfg: dict) -> str:
        return _test_credentials(cfg)

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)
