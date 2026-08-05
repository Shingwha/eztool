"""anydoc（本地文档 → Markdown）provider 测试：纯文本/结构化/HTML 直读 + 引擎格式路由。"""

import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from ezwork_tool.util import CATEGORY_EMPTY, CATEGORY_INVALID, ServiceError
from ezwork_tool.providers.anydoc import (
    AnydocProvider,
    _html_to_markdown,
    _read_text,
)


def _fake_anydoc(md: str = "fake markdown", exc: Exception | None = None):
    """构造假的 anydoc 库模块（引擎格式走它，纯文本/HTML 不经过）。"""
    mod = types.ModuleType("anydoc")

    def to_markdown(path):
        if exc is not None:
            raise exc
        return md

    mod.to_markdown = to_markdown
    return mod


class TestReadText(unittest.TestCase):
    def test_utf8(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
            f.write("你好 hello".encode("utf-8"))
        try:
            self.assertEqual(_read_text(f.name), "你好 hello")
        finally:
            os.unlink(f.name)

    def test_gbk_fallback(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
            f.write("中文内容".encode("gbk"))
        try:
            self.assertEqual(_read_text(f.name), "中文内容")
        finally:
            os.unlink(f.name)

    def test_bom_stripped(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
            f.write(b"\xef\xbb\xbfhello")
        try:
            self.assertEqual(_read_text(f.name), "hello")
        finally:
            os.unlink(f.name)


class TestHtmlToMarkdown(unittest.TestCase):
    def test_headings_paragraphs_links(self):
        html = ("<html><head><title>x</title></head><body>"
                "<h1>Title</h1><p>Hello <a href='https://a.b'>link</a>!</p>"
                "<h2>Sub</h2></body></html>")
        md = _html_to_markdown(html)
        self.assertIn("# Title", md)
        self.assertIn("[link](https://a.b)", md)
        self.assertIn("## Sub", md)
        self.assertNotIn("<h1>", md)

    def test_table(self):
        html = ("<table><tr><th>A</th><th>B</th></tr>"
                "<tr><td>1</td><td>2</td></tr></table>")
        md = _html_to_markdown(html)
        lines = md.splitlines()
        self.assertEqual(lines[0], "| A | B |")
        self.assertIn("| --- | --- |", lines)
        self.assertIn("| 1 | 2 |", lines)

    def test_list_and_code(self):
        html = ("<ul><li>one</li><li>two</li></ul>"
                "<pre><code>print(1)</code></pre>")
        md = _html_to_markdown(html)
        self.assertIn("- one", md)
        self.assertIn("- two", md)
        self.assertIn("```", md)
        self.assertIn("print(1)", md)

    def test_script_style_stripped(self):
        html = ("<p>keep</p><script>var x=1;</script>"
                "<style>.a{}</style><nav>menu</nav>")
        md = _html_to_markdown(html)
        self.assertIn("keep", md)
        self.assertNotIn("var x", md)
        self.assertNotIn(".a{}", md)
        self.assertNotIn("menu", md)

    def test_inline_styles(self):
        md = _html_to_markdown("<p><strong>b</strong> <em>i</em> <code>c</code></p>")
        self.assertIn("**b**", md)
        self.assertIn("*i*", md)
        self.assertIn("`c`", md)


class TestAnydocProvider(unittest.TestCase):
    def setUp(self):
        self.p = AnydocProvider()

    def _file(self, suffix: str, data: bytes):
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.addCleanup(os.unlink, path)
        return path

    def test_unsupported_extension_is_invalid(self):
        path = self._file(".xyz", b"x")
        with self.assertRaises(ServiceError) as ctx:
            self.p.convert_file(path)
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)

    def test_missing_file_is_invalid(self):
        with self.assertRaises(ServiceError) as ctx:
            self.p.convert_file("C:/definitely/missing/file.pdf")
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)

    def test_txt_passthrough(self):
        path = self._file(".txt", "hello 世界\n".encode("utf-8"))
        r = self.p.convert_file(path)
        self.assertEqual(r.provider, "anydoc")
        self.assertIn("hello 世界", r.content)

    def test_md_passthrough(self):
        path = self._file(".md", b"# T\n\nbody")
        r = self.p.convert_file(path)
        self.assertEqual(r.content, "# T\n\nbody")

    def test_json_wrapped_in_code_block(self):
        path = self._file(".json", b'{"a": 1}')
        r = self.p.convert_file(path)
        self.assertEqual(r.content, '```json\n{"a": 1}\n```')

    def test_html_converted(self):
        path = self._file(".html", b"<h1>Hi</h1><p>body</p>")
        r = self.p.convert_file(path)
        self.assertIn("# Hi", r.content)
        self.assertIn("body", r.content)

    def test_engine_format_uses_library(self):
        path = self._file(".docx", b"fake docx")
        with mock.patch.dict(sys.modules, {"anydoc": _fake_anydoc("engine md")}):
            r = self.p.convert_file(path)
        self.assertEqual(r.content, "engine md")

    def test_engine_convert_error_degrades_to_cloud(self):
        path = self._file(".pdf", b"%PDF fake")
        exc = RuntimeError("PDF has no extractable text (Scanned): OCR is required")
        with mock.patch.dict(sys.modules, {"anydoc": _fake_anydoc(exc=exc)}):
            with self.assertRaises(ServiceError) as ctx:
                self.p.convert_file(path)
        self.assertEqual(ctx.exception.category, CATEGORY_EMPTY)

    def test_engine_oserror_is_invalid(self):
        path = self._file(".pdf", b"%PDF fake")
        with mock.patch.dict(sys.modules, {"anydoc": _fake_anydoc(exc=OSError("denied"))}):
            with self.assertRaises(ServiceError) as ctx:
                self.p.convert_file(path)
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)

    def test_empty_output_degrades(self):
        path = self._file(".txt", b"   \n\n  ")
        with self.assertRaises(ServiceError) as ctx:
            self.p.convert_file(path)
        self.assertEqual(ctx.exception.category, CATEGORY_EMPTY)

    def test_missing_library_skippable(self):
        path = self._file(".pdf", b"%PDF fake")
        with mock.patch.dict(sys.modules):
            sys.modules.pop("anydoc", None)
            with self.assertRaises(ServiceError) as ctx:
                self.p.convert_file(path)
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)


if __name__ == "__main__":
    unittest.main()
