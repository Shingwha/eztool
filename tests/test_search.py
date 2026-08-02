"""搜索 failover 链与参数归属校验测试。"""

import unittest

from ezwork_tool import api
from ezwork_tool.base import Provider, SearchResponse, SearchResult
from ezwork_tool.errors import ServiceError, UsageError
from ezwork_tool.registry import SERVICES


class _FakeProvider(Provider):
    """可配置行为的假搜索服务商（类级属性，create_service 新建实例仍生效）。"""

    name = ""
    capabilities = frozenset({"search"})
    fail_with = None
    results = []

    def has_credentials(self, cfg):
        return True

    def search(self, cfg, query, opts):
        if self.fail_with:
            raise self.fail_with
        return SearchResponse(query=query, results=self.results, metadata={})


def _make(name, **attrs):
    cls = type(f"Fake{name.title()}", (_FakeProvider,), {"name": name})
    for k, v in attrs.items():
        setattr(cls, k, v)
    return cls


class TestSearchFailover(unittest.TestCase):
    def setUp(self):
        SERVICES["aaa"] = _make("aaa", results=[SearchResult(title="t", url="u")])
        SERVICES["bbb"] = _make("bbb", results=[SearchResult(title="t2", url="u2")])
        self._old_order = api.AUTO_SEARCH_ORDER
        api.AUTO_SEARCH_ORDER = ["aaa", "bbb"]

    def tearDown(self):
        api.AUTO_SEARCH_ORDER = self._old_order
        SERVICES.pop("aaa", None)
        SERVICES.pop("bbb", None)

    def test_first_success_wins(self):
        resp = api.search({}, "q", "auto")
        self.assertEqual(resp.metadata["backend"], "aaa")

    def test_failure_falls_back_to_next(self):
        SERVICES["aaa"].fail_with = ServiceError("boom", "http")
        resp = api.search({}, "q", "auto")
        self.assertEqual(resp.metadata["backend"], "bbb")

    def test_all_failed_raises(self):
        SERVICES["aaa"].fail_with = ServiceError("boom", "http")
        SERVICES["bbb"].fail_with = ServiceError("boom", "http")
        with self.assertRaises(ServiceError) as ctx:
            api.search({}, "q", "auto")
        self.assertEqual(ctx.exception.code, "search_failed")

    def test_explicit_backend_single(self):
        resp = api.search({}, "q", "aaa")
        self.assertEqual(resp.metadata["backend"], "aaa")
        # 显式后端失败不回退
        SERVICES["aaa"].fail_with = ServiceError("boom", "http")
        with self.assertRaises(ServiceError):
            api.search({}, "q", "aaa")

    def test_unknown_backend(self):
        with self.assertRaises(UsageError):
            api.search({}, "q", "nope")


class TestCheckParams(unittest.TestCase):
    def test_own_param_ok(self):
        api._check_params(["doubao"], {"image": True, "sites": "a.com"})
        api._check_params(["anysearch"], {"tag": "x", "zone": "cn"})

    def test_foreign_param_rejected(self):
        with self.assertRaises(UsageError):
            api._check_params(["doubao"], {"tag": "general.general"})
        with self.assertRaises(UsageError):
            api._check_params(["anysearch"], {"image": True})

    def test_auto_accepts_all(self):
        api._check_params(["doubao", "deepseek", "anysearch"],
                          {"tag": "x", "image": True})  # 不抛

    def test_falsy_params_ignored(self):
        api._check_params(["doubao"], {"tag": None, "anonymous": False})  # 不抛


if __name__ == "__main__":
    unittest.main()
