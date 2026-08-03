"""formatter 输出测试。"""

import unittest

from ezwork_tool.formatter import format_data, format_image, format_search
from ezwork_tool.base import SearchResponse, SearchResult


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


class TestFormatImage(unittest.TestCase):
    def test_direct_link_and_dimensions(self):
        r = SearchResult(title="猫", url="https://x/i.png",
                         extra={"width": 800, "height": 600, "shape": "横长方形"})
        out = format_image(_resp([r]))
        self.assertIn("## Images: q", out)
        self.assertIn("![img](https://x/i.png)", out)
        self.assertIn("800×600", out)
        self.assertIn("横长方形", out)

    def test_missing_dims_omitted(self):
        r = SearchResult(title="猫", url="https://x/i.png")
        out = format_image(_resp([r]))
        self.assertNotIn("×", out)

    def test_footer(self):
        out = format_image(_resp([SearchResult(title="t", url="u")]))
        self.assertIn("backend: anysearch", out)
        self.assertIn("total: 2", out)


class TestFormatData(unittest.TestCase):
    def test_source_annotation(self):
        r = SearchResult(title="AAPL", url="u", snippet="s",
                         extra={"ticker": "AAPL"})
        r.source = "anysearch"
        out = format_data(_resp([r]))
        self.assertIn("## Data Results: q", out)
        self.assertIn("**[anysearch]**", out)

    def test_no_source_no_tag(self):
        out = format_data(_resp([SearchResult(title="t", url="u")]))
        self.assertNotIn("**[", out)


if __name__ == "__main__":
    unittest.main()
