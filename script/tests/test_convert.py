"""convert 测试：URL/文件路由、回退链、并行取先成功、单跑。"""

import os
import tempfile
import unittest

from ezwork_tool import api, provider as prov
from ezwork_tool.provider import FetchResult, Provider
from ezwork_tool.util import ServiceError, UsageError

_SAVED = dict(prov.SERVICES)


class FakeFetchProvider(Provider):
    fail_with = None
    content = "ok"
    low_quality = False
    calls = 0

    def fetch(self, url, timeout=30):
        FakeFetchProvider.calls += 1
        if self.fail_with:
            raise self.fail_with
        return FetchResult(provider=self.name, content=self.content, url=url,
                           elapsed=0.1, low_quality=self.low_quality)

    def convert_file(self, path, timeout=60):
        return self.fetch(path, timeout)


def _install(name, category="convert.page", **attrs):
    cls = type(f"Fake{name.title()}", (FakeFetchProvider,),
               {"name": name, "categories": frozenset({category})})
    for k, v in attrs.items():
        setattr(cls, k, v)
    prov.SERVICES[name] = cls
    return cls


def setUpModule():
    prov.SERVICES.clear()
    prov.SERVICES.update(_SAVED)


def tearDownModule():
    prov.SERVICES.clear()
    prov.SERVICES.update(_SAVED)


class TestConvertRouting(unittest.TestCase):
    def test_url_routes_to_page_chain(self):
        _install("page_a", category="convert.page")
        cfg = {"convert": {"page": {"providers": ["page_a"]}}}
        result = api.convert(cfg, "https://example.com", {})
        self.assertEqual(result.provider, "page_a")

    def test_local_file_routes_to_file_chain(self):
        _install("file_a", category="convert.file")
        cfg = {"convert": {"file": {"providers": ["file_a"]}}}
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("hello")
            path = f.name
        try:
            result = api.convert(cfg, path, {})
            self.assertEqual(result.provider, "file_a")
        finally:
            os.unlink(path)

    def test_missing_file_is_usage_error(self):
        with self.assertRaises(UsageError):
            api.convert({}, "/no/such/file.md", {})

    def test_bad_protocol_treated_as_file(self):
        _install("file_b", category="convert.file")
        cfg = {"convert": {"file": {"providers": ["file_b"]}}}
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            result = api.convert(cfg, path, {})
            self.assertEqual(result.provider, "file_b")
        finally:
            os.unlink(path)


class TestConvertChain(unittest.TestCase):
    def setUp(self):
        FakeFetchProvider.fail_with = None
        FakeFetchProvider.calls = 0
        _install("chain_x", category="convert.page")

    def tearDown(self):
        prov.SERVICES.pop("chain_x", None)

    def test_fallback_to_next(self):
        _install("chain_y", category="convert.page", fail_with=ServiceError("boom"))
        FakeFetchProvider.fail_with = ServiceError("boom")
        _install("chain_z", category="convert.page")
        FakeFetchProvider.fail_with = None
        cfg = {"convert": {"page": {"providers": ["chain_y", "chain_z"]}}}
        result = api.convert(cfg, "https://example.com", {})
        self.assertEqual(result.provider, "chain_z")

    def test_low_quality_keeps_backup(self):
        _install("chain_bad", category="convert.page", content="short",
                 low_quality=True)
        _install("chain_good", category="convert.page")
        cfg = {"convert": {"page": {"providers": ["chain_bad", "chain_good"]}}}
        result = api.convert(cfg, "https://example.com", {})
        self.assertEqual(result.provider, "chain_good")

    def test_all_failed(self):
        _install("chain_f", category="convert.page", fail_with=ServiceError("boom"))
        cfg = {"convert": {"page": {"providers": ["chain_f"]}}}
        with self.assertRaises(ServiceError) as ctx:
            api.convert(cfg, "https://example.com", {})
        self.assertEqual(ctx.exception.code, "convert_failed")


class TestConvertParallel(unittest.TestCase):
    def setUp(self):
        FakeFetchProvider.fail_with = None
        FakeFetchProvider.calls = 0
        _install("par_a", category="convert.page")
        _install("par_b", category="convert.page")

    def tearDown(self):
        prov.SERVICES.pop("par_a", None)
        prov.SERVICES.pop("par_b", None)

    def test_first_success_wins(self):
        result = api.convert({}, "https://example.com",
                             {"providers": "par_a,par_b"})
        self.assertIn(result.provider, ("par_a", "par_b"))

    def test_partial_failure_ok(self):
        _install("par_f", category="convert.page", fail_with=ServiceError("boom"))
        result = api.convert({}, "https://example.com", {"providers": "par_f,par_a"})
        self.assertEqual(result.provider, "par_a")

    def test_all_failed(self):
        _install("par_f2", category="convert.page", fail_with=ServiceError("boom"))
        _install("par_f3", category="convert.page", fail_with=ServiceError("boom"))
        with self.assertRaises(ServiceError):
            api.convert({}, "https://example.com", {"providers": "par_f2,par_f3"})

    def test_single_run(self):
        result = api.convert({}, "https://example.com", {"providers": "par_a"})
        self.assertEqual(result.provider, "par_a")


if __name__ == "__main__":
    unittest.main()
