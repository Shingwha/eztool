"""输出格式化：format_search / format_summary。"""

from eztool.format import format_search, format_summary
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


class TestFormatSummary:
    def test_answer_and_citations(self):
        from eztool.summarize import Citation
        out = format_summary(
            "答案 [1][2]",
            [Citation(index=1, title="A", url="https://a/", provider="tavily"),
             Citation(index=2, title="B", url="https://b/")],
            query="问题",
        )
        assert "## Summary: 问题" in out
        assert "### Answer" in out and "答案 [1][2]" in out
        assert "[1] [A](https://a/) **[tavily]**" in out
        assert "[2] [B](https://b/)" in out and "**[B]**" not in out
