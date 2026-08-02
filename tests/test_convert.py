"""convert（本地文件转 Markdown）子系统测试。"""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from ezwork_tool import chain as chainmod
from ezwork_tool import base as pmod
from ezwork_tool.api import convert
from ezwork_tool.providers.markdown_new import (
    SUPPORTED_EXTENSIONS,
    MarkdownNewProvider,
)


def _ok_response(payload: dict) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = 200
    resp.headers = {}
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp  # 支持 with urllib.request.urlopen(...)
    resp.__exit__.return_value = False
    return resp


def _err_response(code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://markdown.new/convert", code, "err", {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


class TestBuildMultipart(unittest.TestCase):
    def test_body_structure(self):
        body, ctype = pmod.build_multipart("file", "a.csv", b"x,y\n1,2", "text/csv")
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="file"; filename="a.csv"', body)
        self.assertIn(b"Content-Type: text/csv", body)
        self.assertTrue(body.endswith(b"--\r\n"))
        self.assertIn(b"x,y\n1,2", body)


class TestConvertFileLocalChecks(unittest.TestCase):
    def setUp(self):
        self.provider = MarkdownNewProvider()

    def test_missing_file_is_invalid(self):
        with self.assertRaises(pmod.ServiceError) as ctx:
            self.provider.convert_file("C:/definitely/missing/file.pdf")
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)

    def test_unsupported_extension_is_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ")
            path = f.name
        try:
            with self.assertRaises(pmod.ServiceError) as ctx:
                self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)
            self.assertIn(".exe", str(ctx.exception))
        finally:
            os.remove(path)

    def test_oversized_file_is_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            with mock.patch(
                "ezwork_tool.providers.markdown_new.os.path.getsize",
                return_value=11 * 1024 * 1024,
            ):
                with self.assertRaises(pmod.ServiceError) as ctx:
                    self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)
        finally:
            os.remove(path)


class TestConvertFileHttp(unittest.TestCase):
    def setUp(self):
        self.provider = MarkdownNewProvider()

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_success_parses_content_and_tokens(self, urlopen):
        urlopen.return_value = _ok_response({
            "success": True,
            "data": {
                "title": "t.pdf", "content": "# T\n\nbody",
                "filename": "t.pdf", "file_type": ".pdf", "tokens": 42,
            },
        })
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name
        try:
            result = self.provider.convert_file(path, timeout=99)
            self.assertEqual(result.content, "# T\n\nbody")
            self.assertEqual(result.tokens, 42)
            self.assertEqual(result.provider, "markdown")
            # multipart POST，不是 GET
            req = urlopen.call_args[0][0]
            self.assertEqual(req.method, "POST")
            self.assertIn("multipart/form-data", req.get_header("Content-type"))
        finally:
            os.remove(path)

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_service_error_json_maps_to_invalid(self, urlopen):
        urlopen.side_effect = _err_response(400, {
            "success": False,
            "error": "Unsupported file type: .exe. Supported: ...",
            "code": "UNSUPPORTED_FORMAT",
        })
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(pmod.ServiceError) as ctx:
                self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)
            self.assertIn("Unsupported file type", str(ctx.exception))
        finally:
            os.remove(path)

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_success_false_without_code_is_http(self, urlopen):
        urlopen.side_effect = _err_response(500, {
            "success": False, "error": "internal boom", "code": "INTERNAL",
        })
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(pmod.ServiceError) as ctx:
                self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_HTTP)
            self.assertIn("internal boom", str(ctx.exception))
        finally:
            os.remove(path)

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_garbage_body_is_empty(self, urlopen):
        resp = mock.MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.read.return_value = b"<html>not json"
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        urlopen.return_value = resp
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(pmod.ServiceError) as ctx:
                self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_EMPTY)
        finally:
            os.remove(path)

    def test_supported_extensions_cover_service_list(self):
        expected = {
            ".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml",
            ".pdf", ".docx", ".odt", ".xlsx", ".xlsm", ".xlsb", ".xls",
            ".et", ".ods", ".numbers", ".jpeg", ".jpg", ".png", ".webp", ".svg",
        }
        self.assertEqual(SUPPORTED_EXTENSIONS, expected)


class TestConvertChain(unittest.TestCase):
    """链会跳过无 convert_file 能力的 provider（jina），继续到 markdown。"""

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_skips_url_only_provider(self, urlopen):
        urlopen.return_value = _ok_response({
            "success": True,
            "data": {"content": "# ok"},
        })
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            logs: list[str] = []
            result = chainmod.convert_chain(
                path, ["jina", "markdown"], pmod.ProviderOpts(), log=logs.append
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.provider, "markdown")
            self.assertTrue(any("jina" in l and "skipped" in l for l in logs))
        finally:
            os.remove(path)


class TestConvertEntry(unittest.TestCase):
    def test_all_failed_raises_backend_error(self):
        from ezwork_tool.errors import ServiceError

        cfg = {"convert": {"providers": ["markdown"], "timeout": 5},
               "providers": {"markdown": {}}}
        with self.assertRaises(ServiceError) as ctx:
            convert(cfg, "C:/definitely/missing/file.pdf")
        self.assertEqual(ctx.exception.code, "convert_failed")


if __name__ == "__main__":
    unittest.main()
