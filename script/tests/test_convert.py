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
from ezwork_tool.providers.anysearch import AnySearchProvider
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
            self.assertEqual(result.provider, "markdown_new")
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
    """链会跳过无 convert.file 类别的 provider（jina_reader），继续到 markdown_new。"""

    @mock.patch("ezwork_tool.providers.markdown_new.urllib.request.urlopen")
    def test_skips_page_only_provider(self, urlopen):
        urlopen.return_value = _ok_response({
            "success": True,
            "data": {"content": "# ok"},
        })
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            logs: list[str] = []
            result = chainmod.convert_chain(
                path, ["jina_reader", "markdown_new"], pmod.ProviderOpts(), log=logs.append
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.provider, "markdown_new")
            self.assertTrue(any("jina_reader" in l and "skipped" in l for l in logs))
        finally:
            os.remove(path)


class TestConvertEntry(unittest.TestCase):
    """convert 按输入类型路由：URL → convert.page 链；本地路径 → convert.file 链。"""

    def test_missing_local_path_is_usage_error(self):
        from ezwork_tool.errors import UsageError

        with self.assertRaises(UsageError):
            convert({}, "C:/definitely/missing/file.pdf")

    @mock.patch("ezwork_tool.api.run_chain")
    def test_url_routes_to_page_chain(self, run_chain):
        run_chain.return_value = (
            pmod.FetchResult(provider="markdown_new", content="# ok",
                             url="https://example.com/a", elapsed=0.1),
            "markdown_new",
        )
        result = convert({}, "https://example.com/a", {})
        self.assertEqual(result.provider, "markdown_new")
        category = run_chain.call_args[0][1]
        self.assertEqual(category, "convert.page")

    @mock.patch("ezwork_tool.api.run_chain")
    def test_local_path_routes_to_file_chain(self, run_chain):
        run_chain.return_value = (
            pmod.FetchResult(provider="markdown_new", content="# ok",
                             url="", elapsed=0.1),
            "markdown_new",
        )
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = convert({}, path, {})
            self.assertEqual(result.provider, "markdown_new")
            category = run_chain.call_args[0][1]
            self.assertEqual(category, "convert.file")
        finally:
            os.remove(path)

    @mock.patch("ezwork_tool.api.run_chain")
    def test_all_failed_raises_convert_error(self, run_chain):
        from ezwork_tool.errors import ServiceError

        run_chain.return_value = None
        with self.assertRaises(ServiceError) as ctx:
            convert({}, "https://example.com/a")
        self.assertEqual(ctx.exception.code, "convert_failed")

    def test_unknown_provider_usage_error(self):
        from ezwork_tool.errors import UsageError

        with self.assertRaises(UsageError):
            convert({}, "https://example.com/a", {"providers": "nope"})

    @mock.patch("ezwork_tool.api.run_chain")
    def test_providers_override_passed_to_chain(self, run_chain):
        from ezwork_tool.base import FetchResult

        run_chain.return_value = (FetchResult(provider="jina_reader", content="# ok",
                                              url="https://example.com/a", elapsed=0.1),
                                  "jina_reader")
        result = convert({}, "https://example.com/a", {"providers": "jina_reader"})
        self.assertEqual(result.provider, "jina_reader")
        names = run_chain.call_args[0][0]
        self.assertEqual(names, ["jina_reader"])


def _mcp_response(payload: dict) -> tuple:
    """MCP 成功响应三元组（http_post 返回形态）。"""
    return (200, {}, json.dumps(payload).encode("utf-8"))


class TestAnysearchExtract(unittest.TestCase):
    """AnySearch MCP extract（convert.page）测试。"""

    def setUp(self):
        self.provider = AnySearchProvider()

    @mock.patch("ezwork_tool.providers.anysearch.http_post")
    def test_success_parses_mcp_text_content(self, http_post):
        http_post.return_value = _mcp_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [
                {"type": "text", "text": "## Example Domain\n\nbody"},
            ]},
        })
        result = self.provider.fetch("https://example.com", timeout=30)
        self.assertEqual(result.content, "## Example Domain\n\nbody")
        self.assertEqual(result.provider, "anysearch")
        self.assertEqual(result.url, "https://example.com")
        # payload 结构：JSON-RPC tools/call → extract 工具
        url, headers, payload, timeout = http_post.call_args[0]
        self.assertEqual(url, "https://api.anysearch.com/mcp")
        body = json.loads(payload)
        self.assertEqual(body["method"], "tools/call")
        self.assertEqual(body["params"]["name"], "extract")
        self.assertEqual(body["params"]["arguments"]["url"], "https://example.com")
        self.assertEqual(headers["Content-Type"], "application/json")

    @mock.patch("ezwork_tool.providers.anysearch.http_post")
    def test_jsonrpc_error_maps_to_http_category(self, http_post):
        http_post.return_value = _mcp_response({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": "extract fetch failed"},
        })
        with self.assertRaises(pmod.ServiceError) as ctx:
            self.provider.fetch("https://example.com")
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_HTTP)
        self.assertIn("extract fetch failed", str(ctx.exception))

    @mock.patch("ezwork_tool.providers.anysearch.http_post")
    def test_empty_content_is_empty_category(self, http_post):
        http_post.return_value = _mcp_response({
            "jsonrpc": "2.0", "id": 1, "result": {"content": []},
        })
        with self.assertRaises(pmod.ServiceError) as ctx:
            self.provider.fetch("https://example.com")
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_EMPTY)

    @mock.patch("ezwork_tool.providers.anysearch.http_post")
    def test_invalid_json_response_is_http_category(self, http_post):
        http_post.return_value = (200, {}, b"<html>not json</html>")
        with self.assertRaises(pmod.ServiceError) as ctx:
            self.provider.fetch("https://example.com")
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_HTTP)

    @mock.patch("ezwork_tool.providers.anysearch.http_post")
    def test_api_key_sets_authorization(self, http_post):
        http_post.return_value = _mcp_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        })
        provider = AnySearchProvider(
            pmod.ProviderOpts(api_keys={"anysearch": "as_sk_test"})
        )
        provider.fetch("https://example.com")
        headers = http_post.call_args[0][1]
        self.assertEqual(headers["Authorization"], "Bearer as_sk_test")


if __name__ == "__main__":
    unittest.main()
