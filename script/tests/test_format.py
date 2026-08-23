"""输出格式化：format_search / format_image / format_data / format_sources。"""

from eztool.format import (
    format_data,
    format_image,
    format_search,
    format_sources,
)
from eztool.provider import SearchResponse, SearchResult


def _resp(results, backend="x", answer=None, **meta):
    return SearchResponse(query="q", results=results, answer=answer,
                          metadata={"backend": backend, **meta})


class TestFormatSearch:
    def test_merged_backend_tags_source(self):
        r = SearchResult(title="T", url="https://a/", snippet="s", source="pa")
        out = format_search(_resp([r], backend="pa,pb"))
        assert "1. [T](https://a/) **[pa]** — s" in out

    def test_single_backend_no_source_tag(self):
        r = SearchResult(title="T", url="https://a/", source="pa")
        out = format_search(_resp([r], backend="pa"))
        assert "**[pa]**" not in out

    def test_snippet_collapsed_to_one_line(self):
        r = SearchResult(title="T", url="u", snippet="line1\n  line2\tline3")
        out = format_search(_resp([r], backend="pa,pb"))
        assert "line1 line2 line3" in out
        assert "line1\n" not in out

    def test_answer_extra_and_meta_footer(self):
        r = SearchResult(title="T", url="u", extra={"page_age": "3d"})
        out = format_search(_resp([r], backend="ds", answer="AI 回答",
                                  total_results=7, search_time_ms=500,
                                  request_id="r1"))
        assert "### Answer" in out and "AI 回答" in out
        assert "_page_age=3d_" in out
        footer = out.split("---\n")[-1]
        assert "backend: ds" in footer and "total: 7" in footer
        assert "0.50s" in footer and "request_id: r1" in footer

    def test_result_without_url_renders_plain(self):
        r = SearchResult(title="NoLink", url="")
        out = format_search(_resp([r], backend="x"))
        assert "1. NoLink" in out and "[NoLink]()" not in out


class TestFormatImage:
    def test_direct_link_and_dimensions(self):
        r = SearchResult(title="pic", url="https://img/x.png",
                         extra={"width": 100, "height": 200,
                                "shape": "方形", "score": 0.9})
        out = format_image(_resp([r], backend="doubao"))
        assert "1. ![img](https://img/x.png) — 100×200 · 方形 · score=0.9 — pic" in out
        bare = format_image(_resp([SearchResult(title="", url="https://img/y.png")],
                                backend="doubao"))
        assert "1. ![img](https://img/y.png)" in bare and "×" not in bare


class TestFormatData:
    def test_source_always_tagged(self):
        # data 输出不做 merged 判断，有 source 就标注
        r = SearchResult(title="AAPL", url="", snippet="quote", source="anysearch")
        out = format_data(_resp([r], backend="anysearch"))
        assert "1. AAPL **[anysearch]** — quote" in out
        assert out.endswith("backend: anysearch")


class TestFormatSources:
    def test_lists_tags(self):
        out = format_sources([("finance.quote", "Real-time quotes"),
                              ("general.general", "General web search")])
        assert "## Available data source tags" in out
        assert "- `finance.quote` — Real-time quotes" in out
