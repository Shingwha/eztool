"""mineru.net provider（文档→Markdown，异步提交→轮询→下载）测试。"""

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

from ezwork_tool.fetch import provider as pmod
from ezwork_tool.fetch.providers.mineru import (
    BASE_URL,
    MAX_FILE_SIZE,
    MinerUProvider,
)

HAPPY_MD = "# 提取结果\n\n表格内容..."


def _resp(payload: dict, status: int = 200) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = status
    resp.headers = {}
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _md_resp(text: str, status: int = 200) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = status
    resp.headers = {}
    resp.read.return_value = text.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_error(code: int, msg: str = "err") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://mineru.net/x", code, msg, {},
        io.BytesIO(json.dumps({"code": code, "msg": msg}).encode("utf-8")),
    )


class TestConvertFileLocalChecks(unittest.TestCase):
    def setUp(self):
        self.provider = MinerUProvider()

    def test_missing_file_is_invalid(self):
        with self.assertRaises(pmod.FetchError) as ctx:
            self.provider.convert_file("C:/definitely/missing/report.pdf")
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)

    def test_unsupported_extension_is_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
            f.write(b"x")
            path = f.name  # .doc NOT supported by the lightweight API
        try:
            with self.assertRaises(pmod.FetchError) as ctx:
                self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)
            self.assertIn(".doc", str(ctx.exception))
        finally:
            os.remove(path)

    def test_oversized_file_is_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            with mock.patch(
                "ezwork_tool.fetch.providers.mineru.os.path.getsize",
                return_value=MAX_FILE_SIZE + 1,
            ):
                with self.assertRaises(pmod.FetchError) as ctx:
                    self.provider.convert_file(path)
            self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)
        finally:
            os.remove(path)


class TestConvertFileFlow(unittest.TestCase):
    def setUp(self):
        self.provider = MinerUProvider()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.tmp.write(b"%PDF-1.4 fake")
        self.tmp.close()

    def tearDown(self):
        os.remove(self.tmp.name)

    def _flow_ok(self, extra_responses=None):
        """Happy path: submit → PUT → done → download markdown."""
        responses = [
            _resp({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
            _md_resp("", status=200),  # PUT upload
            _resp({"code": 0, "data": {"task_id": "t1", "state": "done",
                                       "markdown_url": "https://cdn/full.md"}}),
            _md_resp(HAPPY_MD),
        ]
        if extra_responses:
            responses = responses[:2] + extra_responses + responses[2:]
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            return self.provider.convert_file(self.tmp.name, timeout=60)

    def test_success_returns_markdown(self):
        result = self._flow_ok()
        self.assertEqual(result.provider, "mineru")
        self.assertEqual(result.content, HAPPY_MD)
        self.assertEqual(result.url, self.tmp.name)

    def test_put_sends_empty_content_type(self):
        """OSS 签名要求 PUT 无 Content-Type；空值头是防 urllib 补默认头的关键。"""
        seen = []

        def fake_urlopen(request, *a, **kw):
            seen.append(request)
            return responses.pop(0)

        responses = [
            _resp({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
            _md_resp("", status=200),
            _resp({"code": 0, "data": {"task_id": "t1", "state": "done",
                                       "markdown_url": "https://cdn/full.md"}}),
            _md_resp(HAPPY_MD),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.provider.convert_file(self.tmp.name, timeout=60)
        put_req = seen[1]
        self.assertEqual(put_req.get_method(), "PUT")
        self.assertEqual(put_req.get_header("Content-type"), "")

    def test_submit_payload_has_is_ocr_true(self):
        """is_ocr 默认开启（扫描件 PDF 也走 OCR），提交 payload 必须带 is_ocr: true。"""
        seen = []

        def fake_urlopen(request, *a, **kw):
            seen.append(request)
            return responses.pop(0)

        responses = [
            _resp({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
            _md_resp("", status=200),
            _resp({"code": 0, "data": {"task_id": "t1", "state": "done",
                                       "markdown_url": "https://cdn/full.md"}}),
            _md_resp(HAPPY_MD),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.provider.convert_file(self.tmp.name, timeout=60)
        submit = json.loads(seen[0].data.decode("utf-8"))
        self.assertEqual(submit["file_name"], os.path.basename(self.tmp.name))
        self.assertTrue(submit["is_ocr"])

    def test_polls_until_done(self):
        result = self._flow_ok(extra_responses=[
            _resp({"code": 0, "data": {"task_id": "t1", "state": "running"}}),
        ])
        self.assertEqual(result.content, HAPPY_MD)

    def test_failed_state_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=[
            _resp({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
            _md_resp("", status=200),
            _resp({"code": 0, "data": {"task_id": "t1", "state": "failed",
                                       "err_code": -30003, "err_msg": "page limit exceeded"}}),
        ]):
            with self.assertRaises(pmod.FetchError) as ctx:
                self.provider.convert_file(self.tmp.name, timeout=60)
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_HTTP)
        self.assertIn("page limit", str(ctx.exception))

    def test_task_submit_rejection_is_invalid(self):
        with mock.patch("urllib.request.urlopen", side_effect=[
            _resp({"code": -30002, "msg": "file type not supported"}),
        ]):
            with self.assertRaises(pmod.FetchError) as ctx:
                self.provider.convert_file(self.tmp.name, timeout=60)
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_INVALID)

    def test_poll_timeout_raises_timeout(self):
        running = _resp({"code": 0, "data": {"task_id": "t1", "state": "running"}})
        real_sleep = time.sleep
        with mock.patch("urllib.request.urlopen", side_effect=[
            _resp({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
            _md_resp("", status=200),
        ] + [running] * 100), mock.patch(
            "time.sleep", side_effect=lambda s: real_sleep(0.05)
        ):
            with self.assertRaises(pmod.FetchError) as ctx:
                self.provider.convert_file(self.tmp.name, timeout=1)
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_TIMEOUT)


class TestFetchFlow(unittest.TestCase):
    def setUp(self):
        self.provider = MinerUProvider()

    def test_url_success(self):
        seen = []

        def fake_urlopen(request, *a, **kw):
            seen.append(request)
            return responses.pop(0)

        responses = [
            _resp({"code": 0, "data": {"task_id": "t2"}}),
            _resp({"code": 0, "data": {"task_id": "t2", "state": "done",
                                       "markdown_url": "https://cdn/full.md"}}),
            _md_resp(HAPPY_MD),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.provider.fetch("https://example.com/doc.pdf", timeout=60)
        self.assertEqual(result.content, HAPPY_MD)
        self.assertEqual(result.url, "https://example.com/doc.pdf")
        submit = json.loads(seen[0].data.decode("utf-8"))
        self.assertEqual(submit["url"], "https://example.com/doc.pdf")
        self.assertTrue(submit["is_ocr"])

    def test_http_429_maps_to_http(self):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(429)):
            with self.assertRaises(pmod.FetchError) as ctx:
                self.provider.fetch("https://example.com/doc.pdf", timeout=60)
        self.assertEqual(ctx.exception.category, pmod.CATEGORY_HTTP)
        self.assertEqual(ctx.exception.http_code, 429)


class TestRegistered(unittest.TestCase):
    def test_mineru_registered_and_has_convert_capability(self):
        from ezwork_tool.fetch import list_convert_providers, list_providers
        self.assertIn("mineru", list_providers())
        self.assertIn("mineru", list_convert_providers())
