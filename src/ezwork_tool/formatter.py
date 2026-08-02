"""唯一输出格式：Markdown（无 --json）。"""

from __future__ import annotations

from .base import SearchResponse

SNIPPET_LIMIT = 300


def _one_line(text: str) -> str:
    """把多行摘要压缩成单行。"""
    return " ".join(text.split())


def format_search(resp: SearchResponse, full: bool = False) -> str:
    lines: list[str] = [f"## Search Results: {resp.query}", ""]
    if resp.answer:
        lines += ["### Answer", "", resp.answer.strip(), ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            title = r.title or r.url or f"(no title {i})"
            line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
            if r.snippet:
                snip = r.snippet if full else _one_line(r.snippet)
                if not full and len(snip) > SNIPPET_LIMIT:
                    snip = snip[:SNIPPET_LIMIT] + "…"
                line += f" — {snip}"
            lines.append(line)
            if r.content:
                body = r.content
                if not full and len(body) > SNIPPET_LIMIT:
                    body = body[:SNIPPET_LIMIT] + "…"
                lines.append(f"   {body}")
            if r.extra:
                extra = " · ".join(f"{k}={v}" for k, v in r.extra.items() if v)
                if extra:
                    lines.append(f"   _{extra}_")
        lines.append("")
    meta = resp.metadata or {}
    meta_parts = [f"backend: {meta.get('backend', '?')}"]
    if meta.get("total_results") is not None:
        meta_parts.append(f"total: {meta['total_results']}")
    if meta.get("search_time_ms") is not None:
        meta_parts.append(f"{meta['search_time_ms'] / 1000:.2f}s")
    if meta.get("request_id"):
        meta_parts.append(f"request_id: {meta['request_id']}")
    lines.append("---\n" + " · ".join(meta_parts))
    return "\n".join(lines)


def format_tags(tags: list[tuple[str, str]]) -> str:
    lines = ["## Available data source tags (AnySearch)", ""]
    for name, desc in tags:
        lines.append(f"- `{name}` — {desc}")
    return "\n".join(lines)
