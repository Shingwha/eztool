"""输出格式化（唯一格式：Markdown，结果默认完整输出不截断）。

从 cli.py 独立出来：cli 只管参数解析与分发，这里管展示。
"""

from __future__ import annotations

from .provider import SearchResponse


def _one_line(text: str) -> str:
    """把多行摘要压缩成单行。"""
    return " ".join(text.split())


def _meta_footer(meta: dict | None) -> str:
    meta = meta or {}
    meta_parts = [f"backend: {meta.get('backend', '?')}"]
    if meta.get("total_results") is not None:
        meta_parts.append(f"total: {meta['total_results']}")
    if meta.get("search_time_ms") is not None:
        meta_parts.append(f"{meta['search_time_ms'] / 1000:.2f}s")
    if meta.get("request_id"):
        meta_parts.append(f"request_id: {meta['request_id']}")
    return "---\n" + " · ".join(meta_parts)


def format_search(resp: SearchResponse) -> str:
    merged = "," in str((resp.metadata or {}).get("backend", ""))
    lines: list[str] = [f"## Search Results: {resp.query}", ""]
    if resp.answer:
        lines += ["### Answer", "", resp.answer.strip(), ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            title = r.title or r.url or f"(no title {i})"
            line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
            if merged and r.source:
                line += f" **[{r.source}]**"
            if r.snippet:
                line += f" — {_one_line(r.snippet)}"
            lines.append(line)
            if r.content:
                lines.append(f"   {r.content}")
            if r.extra:
                extra = " · ".join(f"{k}={v}" for k, v in r.extra.items() if v)
                if extra:
                    lines.append(f"   _{extra}_")
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_summary(answer: str, citations: list, query: str | None = None) -> str:
    """--summarize 输出：AI 答案 + 确定性引用表（替代原始结果列表）。

    引用表的链接/provider 标注来自程序编号（summarize.Citation），不是 LLM
    输出——LLM 只在答案里写 [n]。
    """
    lines: list[str] = []
    if query:
        lines += [f"## Summary: {query}", ""]
    lines += ["### Answer", "", answer.strip(), ""]
    if citations:
        lines += ["### Sources", ""]
        for c in citations:
            title = c.title or c.url or f"(source {c.index})"
            line = f"[{c.index}] [{title}]({c.url})" if c.url else f"[{c.index}] {title}"
            if c.provider:
                line += f" **[{c.provider}]**"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)
