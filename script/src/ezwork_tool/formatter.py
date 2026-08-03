"""唯一输出格式：Markdown（无 --json）。"""

from __future__ import annotations

from .base import SearchResponse

SNIPPET_LIMIT = 300


def _one_line(text: str) -> str:
    """把多行摘要压缩成单行。"""
    return " ".join(text.split())


def format_search(resp: SearchResponse, full: bool = False) -> str:
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


def format_paper(resp: SearchResponse, full: bool = False) -> str:
    """论文卡片格式（多源合并时带 per-provider 计数与 source 标签）。"""
    meta = resp.metadata or {}
    backend = meta.get("backend", "?")
    merged = "," in str(backend)
    lines: list[str] = [f"## Papers: {resp.query}", ""]

    sub = f"_{backend} · total {meta.get('total_results', len(resp.results))}_"
    if merged:
        per = meta.get("per_provider") or {}
        if per:
            counts = " · ".join(f"{k} {v}" for k, v in per.items())
            sub = sub[:-1] + f" · {counts}_"
    lines.append(sub)
    lines.append("")

    for i, r in enumerate(resp.results, 1):
        title = r.title or r.url or f"(no title {i})"
        line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
        lines.append(line)

        extra = r.extra or {}
        parts: list[str] = []
        authors = extra.get("authors")
        if isinstance(authors, list) and authors:
            shown = ", ".join(str(a) for a in authors[:3])
            if len(authors) > 3:
                shown += " et al."
            parts.append(shown)
        if extra.get("year"):
            parts.append(str(extra["year"]))
        if extra.get("venue"):
            parts.append(str(extra["venue"]))
        if extra.get("citations") is not None:
            parts.append(f"⭐{extra['citations']}")
        if extra.get("doi"):
            parts.append(f"doi:{extra['doi']}")
        if extra.get("oa_url"):
            parts.append(f"[OA]({extra['oa_url']})")
        if parts:
            # 无摘要的条目把 source 标签放在 meta 行末尾，保证多源合并时来源可辨
            if merged and r.source and not r.snippet:
                parts.append(f"[{r.source}]")
            lines.append(f"   {' · '.join(parts)}")
        elif merged and r.source and not r.snippet:
            lines.append(f"   [{r.source}]")

        if r.snippet:
            snip = _one_line(r.snippet)
            if not full and len(snip) > SNIPPET_LIMIT:
                snip = snip[:SNIPPET_LIMIT] + "…"
            tag = f"[{r.source}] " if merged and r.source else ""
            lines.append(f"   {tag}{snip}")
    if resp.results:
        lines.append("")

    meta_parts = [f"backend: {backend}"]
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


def format_image(resp: SearchResponse, full: bool = False) -> str:
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


def format_data(resp: SearchResponse, full: bool = False) -> str:
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
                snip = _one_line(r.snippet)
                if not full and len(snip) > SNIPPET_LIMIT:
                    snip = snip[:SNIPPET_LIMIT] + "…"
                line += f" — {snip}"
            lines.append(line)
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)
