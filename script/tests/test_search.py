"""search_category 各类别路由测试：回退链过滤、参数归属、image 模式注入。"""

import unittest

from ezwork_tool import api
from ezwork_tool.base import ParamSpec, Provider, SearchResponse, SearchResult
from ezwork_tool.errors import CATEGORY_INVALID, ServiceError, UsageError
from ezwork_tool.registry import CATEGORIES, SERVICES


class _FakeProvider(Provider):
    """可配置行为的假搜索服务商（类级属性，create_service 新建实例仍生效）。"""

    name = ""
    categories = frozenset()
    category_params = {}
    fail_with = None
    results = []
    seen_opts = None

    def has_credentials(self, cfg):
        return True

    def search(self, cfg, query, opts):
        _FakeProvider.seen_opts = dict(opts)
        if self.fail_with:
            raise self.fail_with
        return SearchResponse(query=query, results=self.results, metadata={})


def _make(name, **attrs):
    cls = type(f"Fake{name.title()}", (_FakeProvider,), {"name": name})
    for k, v in attrs.items():
        setattr(cls, k, v)
    return cls


class TestSearchCategoryRouting(unittest.TestCase):
    """回退链：第一个成功即返回；失败自动换下一个；全部失败抛错。"""

    CATEGORY = "search.web"

    def setUp(self):
        self._orig = list(CATEGORIES.get(self.CATEGORY, []))
        CATEGORIES[self.CATEGORY] = ["aaa", "bbb"]
        SERVICES["aaa"] = _make("aaa", categories=frozenset({self.CATEGORY}),
                                results=[SearchResult(title="t", url="u")])
        SERVICES["bbb"] = _make("bbb", categories=frozenset({self.CATEGORY}),
                                results=[SearchResult(title="t2", url="u2")])
        _FakeProvider.seen_opts = None
        _FakeProvider.fail_with = None

    def tearDown(self):
        CATEGORIES[self.CATEGORY] = self._orig
        SERVICES.pop("aaa", None)
        SERVICES.pop("bbb", None)

    def test_first_success_wins(self):
        resp = api.search_category({}, self.CATEGORY, "q")
        self.assertEqual(resp.metadata["backend"], "aaa")

    def test_failure_falls_back_to_next(self):
        SERVICES["aaa"].fail_with = ServiceError("boom", "http")
        resp = api.search_category({}, self.CATEGORY, "q")
        self.assertEqual(resp.metadata["backend"], "bbb")

    def test_all_failed_raises(self):
        SERVICES["aaa"].fail_with = ServiceError("boom", "http")
        SERVICES["bbb"].fail_with = ServiceError("boom", "http")
        with self.assertRaises(ServiceError) as ctx:
            api.search_category({}, self.CATEGORY, "q")
        self.assertEqual(ctx.exception.code, "search_failed")

    def test_providers_override(self):
        resp = api.search_category({}, self.CATEGORY, "q", {"providers": "bbb"})
        self.assertEqual(resp.metadata["backend"], "bbb")

    def test_config_section_order(self):
        cfg = {"search": {"web": {"providers": ["bbb"]}}}
        resp = api.search_category(cfg, self.CATEGORY, "q")
        self.assertEqual(resp.metadata["backend"], "bbb")

    def test_unknown_provider_usage_error(self):
        with self.assertRaises(UsageError):
            api.search_category({}, self.CATEGORY, "q", {"providers": "nope"})

    def test_unknown_category(self):
        with self.assertRaises(ServiceError) as ctx:
            api.search_category({}, "search.xyz", "q")
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)


class TestParamOwnership(unittest.TestCase):
    """参数归属：provider 特有参数只允许在包含其归属 provider 的链上使用。"""

    CATEGORY = "search.web"

    def setUp(self):
        self._orig = list(CATEGORIES.get(self.CATEGORY, []))
        CATEGORIES[self.CATEGORY] = ["aaa", "bbb"]
        SERVICES["aaa"] = _make(
            "aaa", categories=frozenset({self.CATEGORY}),
            category_params={self.CATEGORY: {"tag": ParamSpec()}},
            results=[SearchResult(title="t", url="u")],
        )
        SERVICES["bbb"] = _make("bbb", categories=frozenset({self.CATEGORY}),
                                results=[SearchResult(title="t2", url="u2")])
        _FakeProvider.fail_with = None

    def tearDown(self):
        CATEGORIES[self.CATEGORY] = self._orig
        SERVICES.pop("aaa", None)
        SERVICES.pop("bbb", None)

    def test_own_param_ok(self):
        resp = api.search_category({}, self.CATEGORY, "q", {"tag": "x"})
        self.assertEqual(resp.metadata["backend"], "aaa")
        self.assertEqual(_FakeProvider.seen_opts["tag"], "x")

    def test_foreign_param_rejected(self):
        # tag 归 aaa，但链只含 bbb → UsageError
        with self.assertRaises(UsageError):
            api.search_category({}, self.CATEGORY, "q", {"providers": "bbb", "tag": "x"})

    def test_falsy_params_ignored(self):
        api.search_category({}, self.CATEGORY, "q", {"tag": None})  # 不抛


class TestImageCategory(unittest.TestCase):
    """search image：只路由 doubao 声明；自动注入 image=True（provider 零改动）。"""

    CATEGORY = "search.image"

    def setUp(self):
        self._orig = list(CATEGORIES.get(self.CATEGORY, []))
        CATEGORIES[self.CATEGORY] = ["aaa"]
        SERVICES["aaa"] = _make(
            "aaa", categories=frozenset({self.CATEGORY}),
            category_params={self.CATEGORY: {
                "width_min": ParamSpec(type=int),
                "shapes": ParamSpec(choices=("横长方形", "竖长方形")),
            }},
            results=[SearchResult(title="img", url="https://x/i.png",
                                  extra={"width": 800, "height": 600})],
        )
        _FakeProvider.fail_with = None

    def tearDown(self):
        CATEGORIES[self.CATEGORY] = self._orig
        SERVICES.pop("aaa", None)

    def test_image_flag_injected(self):
        resp = api.search_category({}, self.CATEGORY, "猫", {"width_min": 100})
        self.assertEqual(_FakeProvider.seen_opts["image"], True)
        self.assertEqual(_FakeProvider.seen_opts["width_min"], 100)
        self.assertEqual(resp.results[0].extra["width"], 800)

    def test_unknown_provider_in_category_rejected(self):
        with self.assertRaises(UsageError):
            api.search_category({}, self.CATEGORY, "q", {"providers": "nope"})

    def test_known_but_wrong_category_skipped_then_all_failed(self):
        # anysearch 存在但不支持 search.image → 链跳过 → all providers failed
        with self.assertRaises(ServiceError) as ctx:
            api.search_category({}, self.CATEGORY, "q", {"providers": "anysearch"})
        self.assertEqual(ctx.exception.code, "search_failed")


if __name__ == "__main__":
    unittest.main()
