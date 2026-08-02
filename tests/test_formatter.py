"""formatter 输出测试。"""

import unittest

from ezwork_tool.formatter import format_search
from ezwork_tool.search.base import SearchResponse, SearchResult


def _resp(results=None, answer=None):
    return SearchResponse(
        query="q",
        results=results or [],
        answer=answer,
        metadata={"backend": "anysearch", "total_results": 2},
    )


class TestFormatSearch(unittest.TestCase):
    def test_snippet_one_line_and_truncated(self):
        snip = "line one\nline two " + "x" * 400
        out = format_search(_resp([SearchResult(title="t", url="u", snippet=snip)]))
        self.assertNotIn("line one\nline two", out)
        self.assertIn("line one line two", out)
        result_line = next(l for l in out.splitlines() if "— " in l)
        self.assertTrue(result_line.endswith("…"))

    def test_content_truncated_unless_full(self):
        content = "y" * 1000
        r = SearchResult(title="t", url="u", snippet="s", content=content)
        out = format_search(_resp([r]))
        self.assertIn("y" * 300 + "…", out)
        full = format_search(_resp([r]), full=True)
        self.assertIn("y" * 1000, full)

    def test_answer_section_only_when_present(self):
        out = format_search(_resp([SearchResult(title="t", url="u")], answer="Answer text"))
        self.assertIn("### Answer", out)
        self.assertIn("Answer text", out)
        no_answer = format_search(_resp([SearchResult(title="t", url="u")]))
        self.assertNotIn("### Answer", no_answer)

    def test_metadata_footer(self):
        out = format_search(_resp([SearchResult(title="t", url="u")]))
        self.assertIn("backend: anysearch", out)
        self.assertIn("total: 2", out)

    def test_empty_results(self):
        out = format_search(_resp())
        self.assertIn("## Search Results: q", out)
        self.assertNotIn("### Results", out)


if __name__ == "__main__":
    unittest.main()
