"""AI 内容提炼（``--summarize`` 的统一收口）。

与 provider（内容来源）正交：summarizer 是**内容加工后端**。注册表 +
``summarize.backend`` 配置键是拓展口——首个实现 ``openai`` 覆盖一切
OpenAI 兼容端点（DeepSeek / Doubao Ark / Moonshot / OpenRouter / Ollama…），
将来接非兼容协议（如 Anthropic 原生）= 加一个类 + 一行注册。

引用**确定性生成**：内容先编号 [1] [2]…，prompt 只允许引用编号，链接与
provider 标注由程序输出——LLM 永不自己写 URL（防幻觉）。

配置（全部显式设置，缺任一键 = exit 2 用法错误）：

- ``summarize.backend``：注册表选择器（默认 openai）。
- ``summarize.base_url`` / ``summarize.api_key`` / ``summarize.model``：必需。
- ``summarize.timeout``：请求超时。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .provider import post_json
from .util import CATEGORY_HTTP, ServiceError, UsageError

REQUIRED_KEYS = ("base_url", "api_key", "model")
DEFAULT_TIMEOUT = 120

SYSTEM_PROMPT = (
    "You are a research assistant. Synthesize an answer to the user's request "
    "using ONLY the provided sources.\n"
    "The sources come from multi-source retrieval and heavily overlap: merge "
    "redundant and repeated information — state each fact once, and cite every "
    "source that supports it (e.g. [2][5][9]). Deduplication must never drop "
    "substance: preserve ALL unique facts, specifics and nuances (numbers, "
    "dates, names, versions, caveats), even minor ones; cut repetition and "
    "boilerplate, never content. When sources conflict, present both versions "
    "with their citations instead of picking one silently.\n"
    "Organize the result clearly (headings/bullets where they help). Cite "
    "sources by their numbers like [1], [2] right after each claim; never "
    "invent URLs or source numbers. If the sources are insufficient, say so "
    "plainly. Answer in the same language as the user's request."
)


@dataclass
class Citation:
    """一条引用：编号 + 标题 + 链接 + 命中 provider（程序生成，非 LLM 输出）。"""

    index: int
    title: str
    url: str
    provider: str = ""


@dataclass
class SourceItem:
    """喂给 LLM 的一份内容（搜索结果或抓取页）。"""

    title: str
    url: str
    text: str
    provider: str = ""


@dataclass
class Summary:
    answer: str
    citations: list[Citation] = field(default_factory=list)


# ── 注册表（拓展口，仿 provider 注册表）─────────────────────────────────────
SUMMARIZERS: dict[str, type["Summarizer"]] = {}


def register(cls: type["Summarizer"]) -> type["Summarizer"]:
    if not cls.name:
        raise ValueError(f"Summarizer {cls.__name__} must define a non-empty 'name'")
    if cls.name in SUMMARIZERS:
        raise ValueError(f"duplicate summarizer name: {cls.name}")
    SUMMARIZERS[cls.name] = cls
    return cls


class Summarizer:
    """总结后端基类：吃 system/user prompt，返回 markdown 回答。"""

    name: str = ""

    def complete(self, system: str, user: str, cfg: dict, timeout: int) -> str:
        raise NotImplementedError


@register
class OpenAISummarizer(Summarizer):
    """OpenAI 兼容 chat/completions 端点（DeepSeek/Ark/Moonshot/OpenRouter…）。"""

    name = "openai"

    def complete(self, system: str, user: str, cfg: dict, timeout: int) -> str:
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        status, _hdrs, raw = post_json(
            url, {"Authorization": f"Bearer {cfg['api_key']}"}, body, timeout
        )
        if status != 200:
            raise ServiceError(
                f"summarize returned HTTP {status}", CATEGORY_HTTP, http_code=status
            )
        try:
            data = json.loads(raw.decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError) as e:
            raise ServiceError(
                f"summarize: invalid response: {e}", CATEGORY_HTTP
            ) from None


# ── 配置解析 ─────────────────────────────────────────────────────────────────


def resolve_config(cfg: dict) -> dict:
    """取 ``summarize.*`` 段；缺必需键 = 用法错误（exit 2，含配置指引）。"""
    sec = cfg.get("summarize")
    if not isinstance(sec, dict):
        sec = {}
    missing = [f"summarize.{k}" for k in REQUIRED_KEYS if not sec.get(k)]
    if missing:
        raise UsageError(
            "--summarize requires config: "
            + ", ".join(missing)
            + " (any OpenAI-compatible endpoint; run: "
            "eztool config set summarize.base_url ... / summarize.api_key ... / "
            "summarize.model ...)"
        )
    backend = sec.get("backend") or "openai"
    if backend not in SUMMARIZERS:
        raise UsageError(
            f"unknown summarize.backend '{backend}' "
            f"(available: {', '.join(sorted(SUMMARIZERS))})"
        )
    return sec


# ── prompt 构建（编号 + 预算截断）─────────────────────────────────────────────


def build_user_prompt(
    request: str, items: list[SourceItem]
) -> tuple[str, list[Citation]]:
    """拼装 user prompt：用户 request + 编号内容块；返回 (prompt, 引用表)。"""
    citations: list[Citation] = []
    blocks: list[str] = []
    for it in items:
        text = (it.text or "").strip()
        if not text:
            continue
        citations.append(
            Citation(index=len(citations) + 1, title=it.title or it.url,
                     url=it.url, provider=it.provider)
        )
        origin = f" (via {it.provider})" if it.provider else ""
        blocks.append(f"[{citations[-1].index}] {it.title} — {it.url}{origin}\n{text}")
    user = f"Request: {request}\n\nSources:\n\n" + "\n\n".join(blocks)
    return user, citations


# ── 入口 ─────────────────────────────────────────────────────────────────────


def summarize(
    cfg: dict, request: str, items: list[SourceItem], timeout: int | None = None
) -> Summary:
    """提炼入口：配置校验 → prompt 构建 → 调后端 → Summary（答案 + 引用表）。"""
    sec = resolve_config(cfg)
    if not any((it.text or "").strip() for it in items):
        raise ServiceError("summarize: nothing to summarize (empty content)", CATEGORY_HTTP)
    try:
        eff_timeout = int(timeout or sec.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        eff_timeout = DEFAULT_TIMEOUT
    user, citations = build_user_prompt(request, items)
    answer = SUMMARIZERS[sec.get("backend") or "openai"]().complete(
        SYSTEM_PROMPT, user, sec, eff_timeout
    )
    answer = (answer or "").strip()
    if not answer:
        raise ServiceError("summarize: empty answer", CATEGORY_HTTP)
    return Summary(answer=answer, citations=citations)
