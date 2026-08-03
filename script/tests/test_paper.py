"""多 provider 汇总搜索（fan-out）测试：run_fanout / 合并去重 / paper 入口 / search 逗号后端。"""

import unittest
from unittest import mock

from ezwork_tool import api
from ezwork_tool.base import Provider, SearchResponse, SearchResult
from ezwork_tool.chain import run_fanout
from ezwork_tool.errors import ServiceError, UsageError
from ezwork_tool.registry import SERVICES

CATEGORY_ALL_FAILED = "all_failed"


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


def _register(*names):
    for n in names:
        SERVICES[n] = _make(n, results=[SearchResult(title=f"t-{n}", url=f"u-{n}")])


def _unregister(*names):
    for n in names:
        SERVICES.pop(n, None)


def _res(title="t", url="u", doi=None, citations=None, year=None):
    extra = {}
    if doi:
        extra["doi"] = doi
    if citations is not None:
        extra["citations"] = citations
    if year is not None:
        extra["year"] = year
    return SearchResult(title=title, url=url, extra=extra or None)


class TestRunFanout(unittest.TestCase):
    def setUp(self):
        _register("aaa", "bbb", "ccc")
        SERVICES["fail"] = _make("fail", fail_with=ServiceError("boom", "http"))

    def tearDown(self):
        _unregister("aaa", "bbb", "ccc", "fail")

    def test_all_success_order_preserved(self):
        logs = []
        out = run_fanout(["ccc", "aaa"], "search",
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["ccc", "aaa"])

    def test_partial_failure_keeps_successes(self):
        logs = []
        out = run_fanout(["fail", "aaa"], "search",
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["aaa"])
        self.assertTrue(any("failed" in m for m in logs))

    def test_all_fail_returns_empty(self):
        out = run_fanout(["fail"], "search",
                         lambda svc: svc.search({}, "q", {}), log=lambda m: None)
        self.assertEqual(out, [])

    def test_unknown_provider_logged_and_skipped(self):
        logs = []
        out = run_fanout(["nope", "aaa"], "search",
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["aaa"])

    def test_no_capability_skipped(self):
        SERVICES["nofetch"] = _make("nofetch", capabilities=frozenset({"fetch"}))
        try:
            out = run_fanout(["nofetch"], "search",
                             lambda svc: svc.search({}, "q", {}), log=lambda m: None)
            self.assertEqual(out, [])
        finally:
            _unregister("nofetch")


class TestMergeSearch(unittest.TestCase):
    def _resp(self, results):
        return SearchResponse(query="q", results=results)

    def test_dedup_by_doi_case_insensitive(self):
        paired = [
            ("a", self._resp([_res(title="T1", doi="10.1/X")])),
            ("b", self._resp([_res(title="T1 copy", doi="10.1/x")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].source, "a")
        self.assertEqual(resp.metadata["backend"], "a,b")
        self.assertEqual(resp.metadata["total_results"], 1)
        self.assertEqual(resp.metadata["per_provider"], {"a": 1, "b": 1})

    def test_dedup_by_url_trailing_slash(self):
        paired = [
            ("a", self._resp([_res(title="T", url="https://x.com/a/")])),
            ("b", self._resp([_res(title="T", url="https://x.com/a")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual(len(resp.results), 1)

    def test_dedup_by_normalized_title(self):
        # 无 URL/DOI 时才用 title 做 key；有 URL 走 URL key（互不碰撞）
        paired = [
            ("a", self._resp([_res(title="Hello   World", url="")])),
            ("b", self._resp([_res(title="hello world", url="")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].source, "a")  # first wins

    def test_no_key_keeps_both(self):
        paired = [
            ("a", self._resp([_res(title="t1", url="")])),
            ("b", self._resp([_res(title="t2", url="")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual(len(resp.results), 2)

    def test_source_backfilled(self):
        paired = [
            ("a", self._resp([_res(title="t1", url="u1"), _res(title="t2", url="u2")])),
            ("b", self._resp([_res(title="t3", url="u3")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual([r.source for r in resp.results], ["a", "a", "b"])

    def test_sort_cited(self):
        paired = [
            ("a", self._resp([_res(title="low", url="u1", citations=3),
                              _res(title="high", url="u2", citations=100)])),
            ("b", self._resp([_res(title="none", url="u3")])),
        ]
        resp = api._merge_search(paired, sort="cited")
        self.assertEqual([r.title for r in resp.results], ["high", "low", "none"])

    def test_sort_date(self):
        paired = [
            ("a", self._resp([_res(title="old", url="u1", year=2019),
                              _res(title="new", url="u2", year=2024)])),
        ]
        resp = api._merge_search(paired, sort="date")
        self.assertEqual([r.title for r in resp.results], ["new", "old"])

    def test_no_sort_keeps_group_order(self):
        paired = [
            ("b", self._resp([_res(title="b1", url="u1"), _res(title="b2", url="u2")])),
            ("a", self._resp([_res(title="a1", url="u3")])),
        ]
        resp = api._merge_search(paired)
        self.assertEqual([r.title for r in resp.results], ["b1", "b2", "a1"])


class TestSearchCommaBackend(unittest.TestCase):
    def setUp(self):
        _register("aaa", "bbb")

    def tearDown(self):
        _unregister("aaa", "bbb")

    def test_comma_backend_fanout_merged(self):
        resp = api.search({}, "q", "aaa,bbb")
        self.assertEqual(len(resp.results), 2)
        self.assertEqual(resp.metadata["backend"], "aaa,bbb")
        self.assertEqual({r.source for r in resp.results}, {"aaa", "bbb"})

    def test_single_and_auto_unchanged(self):
        resp = api.search({}, "q", "aaa")
        self.assertEqual(resp.metadata["backend"], "aaa")
        old = api.AUTO_SEARCH_ORDER
        api.AUTO_SEARCH_ORDER = ["bbb"]
        try:
            resp = api.search({}, "q", "auto")
            self.assertEqual(resp.metadata["backend"], "bbb")
        finally:
            api.AUTO_SEARCH_ORDER = old

    def test_unknown_backend_usage_error(self):
        with self.assertRaises(UsageError):
            api.search({}, "q", "aaa,nope")

    def test_all_fail_raises_service_error(self):
        _unregister("bbb")
        SERVICES["zzz"] = _make("zzz", fail_with=ServiceError("boom", "http"))
        try:
            with self.assertRaises(ServiceError) as ctx:
                api.search({}, "q", "zzz")
            self.assertEqual(ctx.exception.category, CATEGORY_ALL_FAILED)
        finally:
            _unregister("zzz")

    def test_param_owner_check_accepts_owner_in_list(self):
        # --tag 归 anysearch；候选含 anysearch 时应通过
        _register("anysearch")
        try:
            api.search({}, "q", "aaa,anysearch", {"tag": "academic.search"})
        finally:
            _unregister("anysearch")


class TestPaperEntry(unittest.TestCase):
    def setUp(self):
        _register("openalex", "arxiv", "crossref")
        self._old_order = api.PAPER_ORDER
        api.PAPER_ORDER = ["openalex", "arxiv", "crossref"]

    def tearDown(self):
        api.PAPER_ORDER = self._old_order
        _unregister("openalex", "arxiv", "crossref")

    def test_default_order_all_merged(self):
        resp = api.paper({}, "q")
        self.assertEqual(resp.metadata["backend"], "openalex,arxiv,crossref")
        self.assertEqual(len(resp.results), 3)

    def test_backend_list_override(self):
        resp = api.paper({}, "q", {"backend": "arxiv,openalex"})
        self.assertEqual(resp.metadata["backend"], "arxiv,openalex")
        self.assertEqual([r.source for r in resp.results], ["arxiv", "openalex"])

    def test_backend_single_source(self):
        resp = api.paper({}, "q", {"backend": "crossref"})
        self.assertEqual(resp.metadata["backend"], "crossref")
        self.assertEqual([r.source for r in resp.results], ["crossref"])

    def test_config_section_order(self):
        cfg = {"paper": {"providers": ["crossref"]}}
        resp = api.paper(cfg, "q")
        self.assertEqual(resp.metadata["backend"], "crossref")

    def test_providers_key_backward_compat(self):
        # 程序化调用仍可用 providers 键（等价 backend 逗号列表）
        resp = api.paper({}, "q", {"providers": "arxiv,openalex"})
        self.assertEqual(resp.metadata["backend"], "arxiv,openalex")

    def test_unknown_backend_usage_error(self):
        with self.assertRaises(UsageError):
            api.paper({}, "q", {"backend": "openalex,nope"})

    def test_all_fail_raises_service_error(self):
        for n in ("openalex", "arxiv", "crossref"):
            SERVICES[n].fail_with = ServiceError("boom", "http")
        try:
            with self.assertRaises(ServiceError) as ctx:
                api.paper({}, "q")
            self.assertEqual(ctx.exception.category, CATEGORY_ALL_FAILED)
        finally:
            for n in ("openalex", "arxiv", "crossref"):
                SERVICES[n].fail_with = None

    def test_merge_dedup_across_providers(self):
        # openalex 与 crossref 都带 DOI（期刊同一版本）→ DOI 去重（大小写不敏感）；
        # arxiv 预印本无 DOI → 独立保留（不同记录，设计如此）
        SERVICES["openalex"].results = [_res(title="same", doi="10.1/x")]
        SERVICES["arxiv"].results = [_res(title="preprint", url="http://arxiv.org/abs/1")]
        SERVICES["crossref"].results = [_res(title="same", doi="10.1/X")]
        resp = api.paper({}, "q")
        self.assertEqual(len(resp.results), 2)
        self.assertEqual(resp.results[0].source, "openalex")  # first wins


if __name__ == "__main__":
    unittest.main()
