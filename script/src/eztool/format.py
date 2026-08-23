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


def format_image(resp: SearchResponse) -> str:
    """图片结果：直链（可渲染）+ 尺寸/形状元数据。"""
    lines: list[str] = [f"## Images: {resp.query}", ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            line = f"{i}. ![img]({r.url})"
            extra = r.extra or {}
            dims: list[str] = []
            if extra.get("width") or extra.get("height"):
                dims.append(f"{extra.get('width', '?')}×{extra.get('height', '?')}")
            if extra.get("shape"):
                dims.append(str(extra["shape"]))
            if extra.get("score") is not None:
                dims.append(f"score={extra['score']}")
            if dims:
                line += f" — {' · '.join(dims)}"
            if r.title:
                line += f" — {_one_line(r.title)}"
            lines.append(line)
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_data(resp: SearchResponse) -> str:
    """专业数据源结果：带来源标注（provider 名）。"""
    lines: list[str] = [f"## Data Results: {resp.query}", ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            title = r.title or r.url or f"(no title {i})"
            line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
            if r.source:
                line += f" **[{r.source}]**"
            if r.snippet:
                line += f" — {_one_line(r.snippet)}"
            lines.append(line)
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_sources(sources: list[tuple[str, str]]) -> str:
    lines = ["## Available data source tags", ""]
    for name, desc in sources:
        lines.append(f"- `{name}` — {desc}")
    return "\n".join(lines)
