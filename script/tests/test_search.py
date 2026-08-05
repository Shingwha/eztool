"""search_category 测试：回退链（凭证过滤）/ 并行合并 / 单跑。"""

import unittest

from ezwork_tool import api, provider as prov
from ezwork_tool.provider import Provider, SearchResponse, SearchResult
from ezwork_tool.util import CredentialsError, ServiceError, UsageError

_SAVED = dict(prov.SERVICES)


class FakeSearchProvider(Provider):
    """可配置行为的假搜索服务商（类级属性）。"""

    fail_with = None
    results = []
    answer = None
    calls = 0
    auth_required = False

    def has_credentials(self, cfg):
        return True

    def search(self, cfg, query, opts):
        FakeSearchProvider.calls += 1
        if self.fail_with:
            raise self.fail_with
        return SearchResponse(
            query=query, results=[SearchResult(**r) for r in self.results],
            answer=self.answer, metadata={},
        )


def _install(name, category="search.web", **attrs):
    cls = type(f"Fake{name.title()}", (FakeSearchProvider,),
               {"name": name, "categories": frozenset({category})})
    for k, v in attrs.items():
        setattr(cls, k, v)
    if "has_credentials" not in attrs:
        cls.has_credentials = lambda self, cfg: True
    prov.SERVICES[name] = cls
    return cls


def setUpModule():
    prov.SERVICES.clear()
    prov.SERVICES.update(_SAVED)


def tearDownModule():
    prov.SERVICES.clear()
    prov.SERVICES.update(_SAVED)


class TestChain(unittest.TestCase):
    """回退链：第一个成功即返回；失败换下一个；全失败报错。"""

    def setUp(self):
        FakeSearchProvider.fail_with = None
        FakeSearchProvider.results = [{"title": "t", "url": "u"}]
        FakeSearchProvider.answer = None
        FakeSearchProvider.calls = 0
        _install("chain_a")
        _install("chain_b")
        _install("chain_c")

    def tearDown(self):
        for n in ("chain_a", "chain_b", "chain_c"):
            prov.SERVICES.pop(n, None)

    def test_first_success_wins(self):
        resp = api.search_category({}, "search.web", "q", {"providers": "chain_a,chain_b"})
        self.assertEqual(resp.metadata["backend"], "chain_a,chain_b")  # 并行：都跑了
        self.assertEqual(FakeSearchProvider.calls, 2)

    def test_chain_fallback(self):
        # 显式配置链：第一个失败 → 换下一个成功
        FakeSearchProvider.fail_with = ServiceError("boom")
        _install("chain_ok", fail_with=None, results=[{"title": "ok", "url": "u2"}])
        cfg = {"search": {"web": {"providers": ["chain_a", "chain_ok"]}}}
        resp = api.search_category(cfg, "search.web", "q")
        self.assertEqual(resp.metadata["backend"], "chain_ok")

    def test_all_failed_raises(self):
        FakeSearchProvider.fail_with = ServiceError("boom")
        with self.assertRaises(ServiceError) as ctx:
            api.search_category({}, "search.web", "q", {"providers": "chain_a"})
        self.assertEqual(ctx.exception.code, "search_failed")

    def test_unknown_provider_is_usage_error(self):
        with self.assertRaises(UsageError):
            api.search_category({}, "search.web", "q", {"providers": "nope"})

    def test_auth_required_skipped_in_chain(self):
        # auth_required 且无凭证 → 链跳过（不调用 search），换下一个
        _install("chain_auth", auth_required=True,
                 has_credentials=lambda self, cfg: False)
        FakeSearchProvider.calls = 0
        cfg = {"search": {"web": {"providers": ["chain_auth", "chain_a"]}}}
        resp = api.search_category(cfg, "search.web", "q")
        self.assertEqual(resp.metadata["backend"], "chain_a")
        self.assertEqual(FakeSearchProvider.calls, 1)  # 只 chain_a 被调用

    def test_explicit_providers_skip_credential_filter(self):
        _install("chain_auth2", auth_required=True,
                 has_credentials=lambda self, cfg: False)
        FakeSearchProvider.calls = 0
        cfg = {"search": {"web": {"providers": ["chain_auth2"]}}}
        with self.assertRaises(CredentialsError):
            api.search_category(cfg, "search.web", "q", {"providers": "chain_auth2"})


class TestParallel(unittest.TestCase):
    """并行：合并去重、来源标注、单失败不影响、全失败报错。"""

    def setUp(self):
        FakeSearchProvider.fail_with = None
        FakeSearchProvider.calls = 0
        _install("par_a", results=[{"title": "a1", "url": "u1"},
                                   {"title": "dup", "url": "same"}])
        _install("par_b", results=[{"title": "b1", "url": "u2"},
                                   {"title": "dup", "url": "same"}])
        _install("par_c", results=[{"title": "c1", "url": "u3"}])

    def tearDown(self):
        for n in ("par_a", "par_b", "par_c"):
            prov.SERVICES.pop(n, None)

    def test_merge_dedup_and_source(self):
        resp = api.search_category({}, "search.web", "q",
                                   {"providers": "par_a,par_b", "count": 2})
        urls = [r.url for r in resp.results]
        self.assertEqual(sorted(urls), sorted(["u1", "u2", "same"]))  # 去重
        sources = {r.source for r in resp.results}
        self.assertLessEqual(sources, {"par_a", "par_b"})
        self.assertEqual(resp.metadata["backend"], "par_a,par_b")

    def test_partial_failure_ok(self):
        FakeSearchProvider.fail_with = ServiceError("boom")
        _install("par_ok", fail_with=None, results=[{"title": "ok", "url": "u9"}])
        resp = api.search_category({}, "search.web", "q",
                                   {"providers": "par_a,par_ok"})
        # par_a 失败、par_ok 成功 → 返回 par_ok 结果
        self.assertIn("par_ok", resp.metadata["backend"])

    def test_all_failed(self):
        FakeSearchProvider.fail_with = ServiceError("boom")
        with self.assertRaises(ServiceError):
            api.search_category({}, "search.web", "q", {"providers": "par_a,par_b"})

    def test_single_provider_is_run(self):
        resp = api.search_category({}, "search.web", "q", {"providers": "par_c"})
        self.assertEqual(resp.metadata["backend"], "par_c")
        self.assertEqual(len(resp.results), 1)


class TestImageMode(unittest.TestCase):
    def setUp(self):
        _install("img_a", category="search.image")

    def tearDown(self):
        prov.SERVICES.pop("img_a", None)

    def test_image_flag_injected(self):
        seen = {}

        class Capture(Provider):
            name = "img_b"
            categories = frozenset({"search.image"})

            def search(self, cfg, query, opts):
                seen.update(opts)
                return SearchResponse(query=query, metadata={})

        prov.SERVICES["img_b"] = Capture
        try:
            api.search_category({}, "search.image", "q", {"providers": "img_b"})
            self.assertTrue(seen.get("image"))
        finally:
            prov.SERVICES.pop("img_b", None)


if __name__ == "__main__":
    unittest.main()
