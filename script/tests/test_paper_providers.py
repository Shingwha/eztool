"""论文搜索 provider（openalex / arxiv / crossref）解析与参数构建测试。

用 mock 替换 urllib.request.urlopen（三个 provider 都是直接调用它），
不依赖真实网络。覆盖：字段解析、extra 键、年份/作者/OA 过滤参数、
排序参数、摘要还原/剥标签、空结果、网络错误分类。
"""

import json
import unittest
from unittest import mock

from ezwork_tool.base import SearchResponse
from ezwork_tool.errors import NoResultsError, ServiceError
from ezwork_tool.providers import arxiv, crossref, openalex

NS = arxiv.NS


class _FakeResp:
    """urllib response 的最小替身（context manager + read）。"""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(body: bytes):
    """把 urlopen 换成返回固定 body 的假实现。"""
    return mock.patch("urllib.request.urlopen", return_value=_FakeResp(body))


def _capture_url(urlopen_mock):
    """取最近一次请求的完整 URL 字符串（便于断言 query 参数）。"""
    return urlopen_mock.call_args[0][0].full_url


# ── OpenAlex ──────────────────────────────────────────────────────────────

OPENALEX_BODY = json.dumps({
    "meta": {"count": 42},
    "results": [{
        "display_name": "Attention Is All You Need",
        "publication_year": 2017,
        "cited_by_count": 128000,
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "id": "https://openalex.org/W1",
        "primary_location": {
            "source": {"display_name": "arXiv"},
            "landing_page_url": "https://arxiv.org/abs/1706.03762",
        },
        "open_access": {"oa_url": "https://arxiv.org/pdf/1706.03762"},
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani"}},
            {"author": {"display_name": "Noam Shazeer"}},
        ],
        "abstract_inverted_index": {
            "attention": [0], "is": [1], "all": [2], "you": [3], "need": [4],
        },
    }],
}).encode("utf-8")


class TestOpenAlexProvider(unittest.TestCase):
    def test_parse_full(self):
        with _patch_urlopen(OPENALEX_BODY) as m:
            resp = openalex._search({}, "attention", {})
        self.assertIsInstance(resp, SearchResponse)
        self.assertEqual(resp.metadata["total_results"], 42)
        r = resp.results[0]
        self.assertEqual(r.title, "Attention Is All You Need")
        self.assertEqual(r.url, "https://arxiv.org/abs/1706.03762")  # landing 优先
        self.assertEqual(r.snippet, "attention is all you need")  # inverted index 还原
        ex = r.extra
        self.assertEqual(ex["authors"], ["Ashish Vaswani", "Noam Shazeer"])
        self.assertEqual(ex["year"], 2017)
        self.assertEqual(ex["citations"], 128000)
        self.assertEqual(ex["doi"], "10.48550/arxiv.1706.03762")  # 剥前缀
        self.assertEqual(ex["oa_url"], "https://arxiv.org/pdf/1706.03762")

    def test_url_fallback_doi_then_id(self):
        body = json.dumps({"meta": {"count": 1}, "results": [
            {"display_name": "t", "publication_year": 2020, "cited_by_count": 0,
             "doi": "https://doi.org/10.1234/x", "id": "https://openalex.org/W9",
             "primary_location": None, "open_access": {}, "authorships": []}
        ]}).encode()
        with _patch_urlopen(body) as m:
            resp = openalex._search({}, "q", {})
        self.assertEqual(resp.results[0].url, "https://doi.org/10.1234/x")

    def test_year_author_oa_filters_and_two_stage_sort(self):
        with _patch_urlopen(OPENALEX_BODY) as m:
            openalex._search({}, "q", {"year": "2020-2024", "author": "Vaswani",
                                       "oa": True, "sort": "cited"})
        url = _capture_url(m)
        self.assertIn("filter=from_publication_date%3A2020-01-01%2Cto_publication_date%3A2024-12-31%2Craw_author_name.search%3AVaswani%2Copen_access.is_oa%3Atrue", url)
        # 两阶段排序：不再向 API 传全局 sort，而是放大 per-page 取相关性候选集
        self.assertNotIn("sort=", url)
        self.assertIn("per-page=50", url)  # count=10 → max(10*5, 50)
        self.assertNotIn("mailto", url)

    def test_two_stage_sort_ranks_and_truncates(self):
        # 候选集 3 条（引用 1/100/50），sort=cited count=2 → 取 [100, 50]
        body = json.dumps({"meta": {"count": 3}, "results": [
            {"display_name": "low", "publication_year": 2020, "cited_by_count": 1,
             "doi": None, "id": "https://openalex.org/W1", "primary_location": None,
             "open_access": {}, "authorships": []},
            {"display_name": "high", "publication_year": 2020, "cited_by_count": 100,
             "doi": None, "id": "https://openalex.org/W2", "primary_location": None,
             "open_access": {}, "authorships": []},
            {"display_name": "mid", "publication_year": 2020, "cited_by_count": 50,
             "doi": None, "id": "https://openalex.org/W3", "primary_location": None,
             "open_access": {}, "authorships": []},
        ]}).encode()
        with _patch_urlopen(body) as m:
            resp = openalex._search({}, "q", {"sort": "cited", "count": 2})
        self.assertEqual([r.title for r in resp.results], ["high", "mid"])
        self.assertIn("per-page=50", _capture_url(m))

    def test_relevance_keeps_api_default(self):
        with _patch_urlopen(OPENALEX_BODY) as m:
            openalex._search({}, "q", {})
        url = _capture_url(m)
        self.assertIn("per-page=10", url)
        self.assertNotIn("sort=", url)

    def test_mailto_from_cfg(self):
        with _patch_urlopen(OPENALEX_BODY) as m:
            openalex._search({"providers": {"openalex": {"mailto": "me@x.com"}}}, "q", {})
        self.assertIn("mailto=me%40x.com", _capture_url(m))

    def test_empty_results_raises_no_results(self):
        body = json.dumps({"meta": {"count": 0}, "results": []}).encode()
        with _patch_urlopen(body):
            with self.assertRaises(NoResultsError):
                openalex._search({}, "q", {})


# ── arXiv ─────────────────────────────────────────────────────────────────

ARXIV_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>123</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/1706.03762</id>
    <title>Attention Is All You Need</title>
    <published>2017-06-12T17:03:04Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
    <journal_ref>NeurIPS 2017</journal_ref>
    <summary>We propose a new simple network architecture,
    the Transformer, based solely on attention.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2024.00001</id>
    <title>Old Paper Out of Range</title>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Someone Else</name></author>
    <summary>Should be filtered out by year 2023.</summary>
  </entry>
</feed>""".encode("utf-8")


class TestArxivProvider(unittest.TestCase):
    def test_parse_full(self):
        with _patch_urlopen(ARXIV_BODY) as m:
            resp = arxiv._search({}, "attention", {})
        self.assertEqual(resp.metadata["total_results"], 123)  # opensearch 计数
        r = resp.results[0]
        self.assertEqual(r.title, "Attention Is All You Need")
        self.assertEqual(r.url, "http://arxiv.org/abs/1706.03762")
        self.assertEqual(r.extra["authors"], ["Ashish Vaswani", "Noam Shazeer"])
        self.assertEqual(r.extra["year"], 2017)
        self.assertEqual(r.extra["venue"], "NeurIPS 2017")
        self.assertEqual(r.extra["doi"], "10.48550/arXiv.1706.03762")
        self.assertNotIn("citations", r.extra)  # arXiv 无引用数
        self.assertNotIn("oa_url", r.extra)
        self.assertIn("Transformer", r.snippet)

    def test_year_post_filter(self):
        # fixture: 2017 与 2024 两条；year=2017 保留第一条、过滤 2024 条
        with _patch_urlopen(ARXIV_BODY) as m:
            resp = arxiv._search({}, "attention", {"year": "2017"})
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].title, "Attention Is All You Need")
        self.assertIn("sortBy=relevance", _capture_url(m))

    def test_author_quoting_and_date_sort(self):
        with _patch_urlopen(ARXIV_BODY) as m:
            arxiv._search({}, "attention is all you need", {"author": "A Vaswani",
                                                            "sort": "date"})
        url = _capture_url(m)
        self.assertIn("search_query=au%3A%22A+Vaswani%22+AND+all%3A%22attention+is+all+you+need%22", url)
        self.assertIn("sortBy=submittedDate", url)
        self.assertIn("sortOrder=descending", url)

    def test_multiword_query_quoted(self):
        with _patch_urlopen(ARXIV_BODY) as m:
            arxiv._search({}, "attention is all you need", {})
        self.assertIn("search_query=all%3A%22attention+is+all+you+need%22", _capture_url(m))

    def test_empty_raises_no_results(self):
        empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
        with _patch_urlopen(empty):
            with self.assertRaises(NoResultsError):
                arxiv._search({}, "q", {})


# ── Crossref ──────────────────────────────────────────────────────────────

CROSSREF_BODY = json.dumps({
    "message": {
        "total-results": 7,
        "items": [{
            "DOI": "10.1109/tpami.2022.3152247",
            "title": ["A Survey on Vision Transformer"],
            "container-title": ["IEEE TPAMI"],
            "issued": {"date-parts": [[2023]]},
            "is-referenced-by-count": 4109,
            "author": [{"given": "Kai", "family": "Han"},
                       {"given": "Yunhe", "family": "Wang"}],
            "link": [{"content-type": "text/html", "URL": "https://x.org/a.pdf"}],
            "abstract": "<jats:p>We survey <jats:bold>transformers</jats:bold>.</jats:p>",
        }],
    },
}).encode("utf-8")


class TestCrossrefProvider(unittest.TestCase):
    def test_parse_full(self):
        with _patch_urlopen(CROSSREF_BODY) as m:
            resp = crossref._search({}, "vision transformer", {})
        self.assertEqual(resp.metadata["total_results"], 7)
        r = resp.results[0]
        self.assertEqual(r.title, "A Survey on Vision Transformer")
        self.assertEqual(r.url, "https://doi.org/10.1109/tpami.2022.3152247")
        self.assertEqual(r.extra["authors"], ["Kai Han", "Yunhe Wang"])
        self.assertEqual(r.extra["year"], 2023)
        self.assertEqual(r.extra["venue"], "IEEE TPAMI")
        self.assertEqual(r.extra["citations"], 4109)
        self.assertEqual(r.extra["doi"], "10.1109/tpami.2022.3152247")
        self.assertEqual(r.extra["oa_url"], "https://x.org/a.pdf")
        self.assertEqual(r.snippet, "We survey transformers .")  # JATS 剥标签（标签→空格）

    def test_year_author_oa_filter_and_two_stage_sort(self):
        with _patch_urlopen(CROSSREF_BODY) as m:
            crossref._search({}, "q", {"year": "2020-2024", "author": "Han",
                                       "oa": True, "sort": "cited"})
        url = _capture_url(m)
        self.assertIn("query.author=Han", url)
        self.assertIn("filter=from-pub-date%3A2020-01-01%2Cuntil-pub-date%3A2024-12-31%2Chas-full-text%3Atrue", url)
        # 两阶段排序：不向 API 传全局 sort/order，rows 放大取相关性候选集
        self.assertNotIn("sort=", url)
        self.assertNotIn("order=", url)
        self.assertIn("rows=50", url)

    def test_figure_entries_filtered(self):
        items = json.loads(CROSSREF_BODY)["message"]["items"]
        fig = {"DOI": "10.1/fig", "title": ["Figure 6: Vision transformer (ViT) model structure."],
               "container-title": [""], "issued": {"date-parts": [[2021]]},
               "is-referenced-by-count": 0, "author": []}
        body = json.dumps({"message": {"total-results": 2, "items": items + [fig]}}).encode()
        with _patch_urlopen(body):
            resp = crossref._search({}, "q", {})
        self.assertEqual(len(resp.results), 1)  # Figure 条目被过滤
        self.assertNotIn("Figure", resp.results[0].title)

    def test_two_stage_sort_ranks_and_truncates(self):
        base = json.loads(CROSSREF_BODY)["message"]["items"][0]
        items = []
        for i, c in ((1, 3), (2, 100), (3, 50)):
            it = dict(base)
            it["DOI"] = f"10.1/x{i}"
            it["is-referenced-by-count"] = c
            it["issued"] = {"date-parts": [[2020]]}
            items.append(it)
        body = json.dumps({"message": {"total-results": 3, "items": items}}).encode()
        with _patch_urlopen(body) as m:
            resp = crossref._search({}, "q", {"sort": "cited", "count": 2})
        self.assertEqual([r.extra["citations"] for r in resp.results], [100, 50])
        self.assertIn("rows=50", _capture_url(m))

    def test_year_fallback_published_online(self):
        item = json.loads(CROSSREF_BODY)["message"]["items"][0]
        self.assertEqual(crossref._pub_year(item), 2023)
        item2 = dict(item)
        item2.pop("issued")
        item2["published-online"] = {"date-parts": [[2019, 5]]}
        self.assertEqual(crossref._pub_year(item2), 2019)
        item3 = dict(item)
        item3.pop("issued")
        item3["published-online"] = {"date-parts": [[]]}
        self.assertIsNone(crossref._pub_year(item3))


if __name__ == "__main__":
    unittest.main()
