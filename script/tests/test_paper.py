"""search paper（三源并行 fan-out）与合并去重测试：run_fanout / _merge_search / paper 入口。"""

import unittest
from unittest import mock

from ezwork_tool import api
from ezwork_tool.base import Provider, SearchResponse, SearchResult
from ezwork_tool.chain import run_fanout
from ezwork_tool.errors import ServiceError, UsageError
from ezwork_tool.registry import CATEGORIES, SERVICES

CATEGORY_ALL_FAILED = "all_failed"
PAPER_CATEGORY = "search.paper"


class _FakeProvider(Provider):
    """可配置行为的假搜索服务商（类级属性，create_service 新建实例仍生效）。"""

    name = ""
    categories = frozenset()
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
        SERVICES[n] = _make(n, categories=frozenset({PAPER_CATEGORY}),
                            results=[SearchResult(title=f"t-{n}", url=f"u-{n}")])


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
        SERVICES["fail"] = _make("fail", categories=frozenset({PAPER_CATEGORY}),
                                 fail_with=ServiceError("boom", "http"))

    def tearDown(self):
        _unregister("aaa", "bbb", "ccc", "fail")

    def test_all_success_order_preserved(self):
        logs = []
        out = run_fanout(["ccc", "aaa"], PAPER_CATEGORY,
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["ccc", "aaa"])

    def test_partial_failure_keeps_successes(self):
        logs = []
        out = run_fanout(["fail", "aaa"], PAPER_CATEGORY,
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["aaa"])
        self.assertTrue(any("failed" in m for m in logs))

    def test_all_fail_returns_empty(self):
        out = run_fanout(["fail"], PAPER_CATEGORY,
                         lambda svc: svc.search({}, "q", {}), log=lambda m: None)
        self.assertEqual(out, [])

    def test_unknown_provider_logged_and_skipped(self):
        logs = []
        out = run_fanout(["nope", "aaa"], PAPER_CATEGORY,
                         lambda svc: svc.search({}, "q", {}), log=logs.append)
        self.assertEqual([n for n, _ in out], ["aaa"])

    def test_wrong_category_skipped(self):
        # 只支持 convert.page 的 provider 在 paper fan-out 中被跳过
        SERVICES["nofetch"] = _make("nofetch", categories=frozenset({"convert.page"}))
        try:
            out = run_fanout(["nofetch"], PAPER_CATEGORY,
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


class TestPaperEntry(unittest.TestCase):
    """search_category(cfg, "search.paper", q, opts)：三源并行汇总 + 去重。"""

    def setUp(self):
        _register("openalex", "arxiv", "crossref")
        self._orig = list(CATEGORIES.get(PAPER_CATEGORY, []))
        CATEGORIES[PAPER_CATEGORY] = ["openalex", "arxiv", "crossref"]

    def tearDown(self):
        CATEGORIES[PAPER_CATEGORY] = self._orig
        _unregister("openalex", "arxiv", "crossref")

    def test_default_order_all_merged(self):
        resp = api.search_category({}, PAPER_CATEGORY, "q")
        self.assertEqual(resp.metadata["backend"], "openalex,arxiv,crossref")
        self.assertEqual(len(resp.results), 3)

    def test_providers_override(self):
        resp = api.search_category({}, PAPER_CATEGORY, "q", {"providers": "arxiv,openalex"})
        self.assertEqual(resp.metadata["backend"], "arxiv,openalex")
        self.assertEqual([r.source for r in resp.results], ["arxiv", "openalex"])

    def test_providers_single_source(self):
        resp = api.search_category({}, PAPER_CATEGORY, "q", {"providers": "crossref"})
        self.assertEqual(resp.metadata["backend"], "crossref")
        self.assertEqual([r.source for r in resp.results], ["crossref"])

    def test_config_section_order(self):
        cfg = {"search": {"paper": {"providers": ["crossref"]}}}
        resp = api.search_category(cfg, PAPER_CATEGORY, "q")
        self.assertEqual(resp.metadata["backend"], "crossref")

    def test_unknown_provider_usage_error(self):
        with self.assertRaises(UsageError):
            api.search_category({}, PAPER_CATEGORY, "q", {"providers": "openalex,nope"})

    def test_all_fail_raises_service_error(self):
        for n in ("openalex", "arxiv", "crossref"):
            SERVICES[n].fail_with = ServiceError("boom", "http")
        try:
            with self.assertRaises(ServiceError) as ctx:
                api.search_category({}, PAPER_CATEGORY, "q")
            self.assertEqual(ctx.exception.category, CATEGORY_ALL_FAILED)
            self.assertEqual(ctx.exception.code, "search_failed")
        finally:
            for n in ("openalex", "arxiv", "crossref"):
                SERVICES[n].fail_with = None

    def test_merge_dedup_across_providers(self):
        # openalex 与 crossref 都带 DOI（期刊同一版本）→ DOI 去重（大小写不敏感）；
        # arxiv 预印本无 DOI → 独立保留（不同记录，设计如此）
        SERVICES["openalex"].results = [_res(title="same", doi="10.1/x")]
        SERVICES["arxiv"].results = [_res(title="preprint", url="http://arxiv.org/abs/1")]
        SERVICES["crossref"].results = [_res(title="same", doi="10.1/X")]
        resp = api.search_category({}, PAPER_CATEGORY, "q")
        self.assertEqual(len(resp.results), 2)
        self.assertEqual(resp.results[0].source, "openalex")  # first wins

    def test_sort_and_year_passed_through(self):
        # --sort/--year 是命令专属参数，原样透传给每个 provider
        seen = {}

        def spy(cfg, query, opts):
            seen["sort"] = opts.get("sort")
            seen["year"] = opts.get("year")
            return SearchResponse(query=query, results=[], metadata={})

        for n in ("openalex", "arxiv", "crossref"):
            SERVICES[n].search = staticmethod(spy)
        api.search_category({}, PAPER_CATEGORY, "q",
                            {"sort": "cited", "year": "2023", "oa": True})
        self.assertEqual(seen, {"sort": "cited", "year": "2023"})


if __name__ == "__main__":
    unittest.main()
