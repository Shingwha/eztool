"""真实 provider 的纯逻辑测试：anydoc / mineru / doubao / anysearch。

全部 mock urlopen（或模块级 http_get/post_json），零真实网络。
"""

import io
import json
import sys
import types
import zipfile
from unittest import mock

import pytest

from eztool.provider import ProviderOpts
from eztool.util import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CredentialsError,
    ServiceError,
)

from conftest import json_response, make_http_error, make_response


def _popts(**sections):
    return ProviderOpts(configs=sections)


# ════════════════════════════════════════════════════════════════════════════
# anydoc
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers.anydoc import (
    AnydocProvider,
    _html_to_markdown,
    _read_text,
)


def _fake_anydoc(md="fake markdown", exc=None):
    """假的 anydoc 库模块（引擎格式走它，纯文本/HTML 不经过）。"""
    mod = types.ModuleType("anydoc")

    def to_markdown(path):
        if exc is not None:
            raise exc
        return md

    mod.to_markdown = to_markdown
    return mod


class TestReadText:
    def test_encoding_detection(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes("你好 hello".encode("utf-8"))
        assert _read_text(str(f)) == "你好 hello"
        f.write_bytes(b"\xef\xbb\xbfhello")  # BOM 剥掉
        assert _read_text(str(f)) == "hello"
        f.write_bytes("中文内容".encode("gbk"))  # GBK 回退
        assert _read_text(str(f)) == "中文内容"


class TestHtmlToMarkdown:
    def test_headings_paragraphs_links(self):
        html = ("<html><head><title>x</title></head><body>"
                "<h1>Title</h1><p>Hello <a href='https://a.b'>link</a>!</p>"
                "<h2>Sub</h2></body></html>")
        md = _html_to_markdown(html)
        assert "# Title" in md and "## Sub" in md
        assert "[link](https://a.b)" in md
        assert "<h1>" not in md

    def test_table(self):
        md = _html_to_markdown(
            "<table><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></table>")
        lines = md.splitlines()
        assert lines[0] == "| A | B |"
        assert "| --- | --- |" in lines and "| 1 | 2 |" in lines

    def test_list_and_code(self):
        md = _html_to_markdown("<ul><li>one</li><li>two</li></ul>"
                               "<pre><code>print(1)</code></pre>")
        assert "- one" in md and "- two" in md
        assert "```" in md and "print(1)" in md

    def test_inline_styles_and_noise_stripped(self):
        md = _html_to_markdown("<p><strong>b</strong> <em>i</em> <code>c</code></p>")
        assert "**b**" in md and "*i*" in md and "`c`" in md
        md = _html_to_markdown("<p>keep</p><script>var x=1;</script>"
                               "<style>.a{}</style><nav>menu</nav>")
        assert "keep" in md
        assert "var x" not in md and ".a{}" not in md and "menu" not in md


class TestAnydocConvertFile:
    p = AnydocProvider()

    def test_unsupported_extension_is_invalid(self, tmp_path):
        f = tmp_path / "a.xyz"
        f.write_bytes(b"x")
        with pytest.raises(ServiceError) as exc:
            self.p.convert_file(str(f))
        assert exc.value.category == CATEGORY_INVALID

    def test_missing_file_is_invalid(self, tmp_path):
        with pytest.raises(ServiceError) as exc:
            self.p.convert_file(str(tmp_path / "missing.pdf"))
        assert exc.value.category == CATEGORY_INVALID

    def test_txt_passthrough(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes("hello 世界\n".encode("utf-8"))
        r = self.p.convert_file(str(f))
        assert r.provider == "anydoc" and "hello 世界" in r.content

    def test_json_wrapped_in_code_block(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_bytes(b'{"a": 1}')
        assert self.p.convert_file(str(f)).content == '```json\n{"a": 1}\n```'

    def test_html_converted(self, tmp_path):
        f = tmp_path / "a.html"
        f.write_bytes(b"<h1>Hi</h1><p>body</p>")
        content = self.p.convert_file(str(f)).content
        assert "# Hi" in content and "body" in content

    def test_engine_format_uses_library(self, tmp_path, monkeypatch):
        f = tmp_path / "a.docx"
        f.write_bytes(b"fake docx")
        monkeypatch.setitem(sys.modules, "anydoc", _fake_anydoc("engine md"))
        assert self.p.convert_file(str(f)).content == "engine md"

    def test_engine_convert_error_degrades(self, tmp_path, monkeypatch):
        # 扫描版 PDF 无文本 → CATEGORY_EMPTY（retriable，链降级云端）
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF fake")
        exc = RuntimeError("PDF has no extractable text (Scanned): OCR is required")
        monkeypatch.setitem(sys.modules, "anydoc", _fake_anydoc(exc=exc))
        with pytest.raises(ServiceError) as e:
            self.p.convert_file(str(f))
        assert e.value.category == CATEGORY_EMPTY

    def test_engine_oserror_is_invalid(self, tmp_path, monkeypatch):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF fake")
        monkeypatch.setitem(sys.modules, "anydoc",
                            _fake_anydoc(exc=OSError("denied")))
        with pytest.raises(ServiceError) as e:
            self.p.convert_file(str(f))
        assert e.value.category == CATEGORY_INVALID

    def test_missing_library_is_skippable(self, tmp_path, monkeypatch):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF fake")
        # sys.modules 里塞 None → import anydoc 直接抛 ImportError（库装没装都成立）
        monkeypatch.setitem(sys.modules, "anydoc", None)
        with pytest.raises(ServiceError) as e:
            self.p.convert_file(str(f))
        assert e.value.category == CATEGORY_INVALID

    def test_empty_output_degrades(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"   \n\n  ")
        with pytest.raises(ServiceError) as e:
            self.p.convert_file(str(f))
        assert e.value.category == CATEGORY_EMPTY


# ════════════════════════════════════════════════════════════════════════════
# mineru
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers import mineru as mineru_mod
from eztool.providers.mineru import MinerUProvider

HAPPY_MD = "# 提取结果\n\n表格内容..."


def _make_zip(md: str = HAPPY_MD) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("uuid/full.md", md)
    return buf.getvalue()


@pytest.fixture
def pdf_file(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    return str(f)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """轮询里的 time.sleep 打住，测试不等真实间隔。"""
    monkeypatch.setattr(mineru_mod.time, "sleep", lambda s: None)


class _Recorder:
    """按队列返回响应，同时记录每个 Request。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def __call__(self, request, *a, **kw):
        self.seen.append(request)
        return self.responses.pop(0)


V1_FLOW = [
    json_response({"code": 0, "data": {"task_id": "t1", "file_url": "https://oss/signed"}}),
    make_response(200, b""),  # PUT upload
    json_response({"code": 0, "data": {"task_id": "t1", "state": "done",
                                       "markdown_url": "https://cdn/full.md"}}),
    make_response(200, HAPPY_MD),
]


class TestMineruV1Flow:
    def test_full_flow_payload_put_and_poll(self, pdf_file, monkeypatch):
        rec = _Recorder(V1_FLOW)
        monkeypatch.setattr("urllib.request.urlopen", rec)
        result = MinerUProvider().convert_file(pdf_file, timeout=60)
        assert result.content == HAPPY_MD and result.provider == "mineru"

        submit = rec.seen[0]
        assert "/api/v1/agent/parse/file" in submit.full_url
        assert submit.get_header("Authorization") is None  # v1 无 token
        payload = json.loads(submit.data.decode("utf-8"))
        assert payload["is_ocr"] is True  # 扫描件也走 OCR

        put = rec.seen[1]
        assert put.get_method() == "PUT"
        assert put.get_header("Content-type") == ""  # 防 urllib 补默认 Content-Type

        assert "/api/v1/agent/parse/t1" in rec.seen[2].full_url

    def test_polls_until_done(self, pdf_file, monkeypatch):
        responses = V1_FLOW[:2] + [
            json_response({"code": 0, "data": {"task_id": "t1", "state": "running"}}),
        ] + V1_FLOW[2:]
        monkeypatch.setattr("urllib.request.urlopen", _Recorder(responses))
        assert MinerUProvider().convert_file(pdf_file, timeout=60).content == HAPPY_MD

    def test_submit_rejection_minus_30002_is_invalid(self, pdf_file, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _Recorder([
            json_response({"code": -30002, "msg": "file type not supported"}),
        ]))
        with pytest.raises(ServiceError) as exc:
            MinerUProvider().convert_file(pdf_file, timeout=60)
        assert exc.value.category == CATEGORY_INVALID

    def test_http_429_maps_to_http(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            mock.Mock(side_effect=make_http_error(429)))
        with pytest.raises(ServiceError) as exc:
            MinerUProvider().fetch("https://example.com/doc.pdf", timeout=60)
        assert exc.value.category == CATEGORY_HTTP
        assert exc.value.http_code == 429

    def test_local_checks(self, tmp_path):
        p = MinerUProvider()
        with pytest.raises(ServiceError) as e1:  # 文件不存在
            p.convert_file(str(tmp_path / "missing.pdf"))
        assert e1.value.category == CATEGORY_INVALID
        f = tmp_path / "a.doc"  # v1 不支持 .doc
        f.write_bytes(b"x")
        with pytest.raises(ServiceError) as e2:
            p.convert_file(str(f))
        assert e2.value.category == CATEGORY_INVALID and ".doc" in str(e2.value)


class TestMineruV4Flow:
    provider = MinerUProvider(_popts(mineru={"api_key": "test-token"}))

    V4_FLOW = [
        json_response({"code": 0, "data": {"batch_id": "b1",
                                           "file_urls": ["https://oss/signed"]}}),
        make_response(200, b""),  # PUT
        json_response({"code": 0, "data": {"batch_id": "b1", "extract_result": [
            {"file_name": "x.pdf", "state": "done",
             "full_zip_url": "https://cdn/r.zip"}]}}),
        make_response(200, _make_zip()),
    ]

    def test_full_flow_bearer_and_zip(self, pdf_file, monkeypatch):
        rec = _Recorder(self.V4_FLOW)
        monkeypatch.setattr("urllib.request.urlopen", rec)
        result = self.provider.convert_file(pdf_file, timeout=60)
        assert result.content == HAPPY_MD  # zip 里取出 full.md

        submit = rec.seen[0]
        assert "/api/v4/file-urls/batch" in submit.full_url
        assert submit.get_header("Authorization") == "Bearer test-token"
        payload = json.loads(submit.data.decode("utf-8"))
        assert payload["model_version"] == "vlm"
        assert payload["files"][0]["is_ocr"] is True
        assert "/api/v4/extract-results/batch/b1" in rec.seen[2].full_url

    def test_html_file_uses_mineru_html_model(self, tmp_path, monkeypatch):
        f = tmp_path / "page.html"
        f.write_bytes(b"<html><body>hi</body></html>")
        rec = _Recorder(self.V4_FLOW)
        monkeypatch.setattr("urllib.request.urlopen", rec)
        self.provider.convert_file(str(f), timeout=60)
        payload = json.loads(rec.seen[0].data.decode("utf-8"))
        assert payload["model_version"] == "MinerU-HTML"

    def test_fetch_html_url_uses_mineru_html_model(self, monkeypatch):
        rec = _Recorder([
            json_response({"code": 0, "data": {"task_id": "t9"}}),
            json_response({"code": 0, "data": {"task_id": "t9", "state": "done",
                                               "full_zip_url": "https://cdn/r.zip"}}),
            make_response(200, _make_zip()),
        ])
        monkeypatch.setattr("urllib.request.urlopen", rec)
        result = self.provider.fetch("https://example.com/page.html?x=1", timeout=60)
        assert result.content == HAPPY_MD
        payload = json.loads(rec.seen[0].data.decode("utf-8"))
        assert payload["model_version"] == "MinerU-HTML"
        assert payload["is_ocr"] is True


class TestMineruDownloadRegression:
    """回归：下载阶段返回非 200 → ServiceError(CATEGORY_HTTP)（此前被当内容解析）。"""

    def test_download_text_non_200(self, monkeypatch):
        monkeypatch.setattr(mineru_mod, "http_get",
                            lambda *a, **kw: (500, {}, b"server error"))
        with pytest.raises(ServiceError) as exc:
            MinerUProvider()._download_text("https://cdn/full.md", 60)
        assert exc.value.category == CATEGORY_HTTP
        assert exc.value.http_code == 500

    def test_download_zip_non_200(self, monkeypatch):
        monkeypatch.setattr(mineru_mod, "http_get",
                            lambda *a, **kw: (403, {}, b"denied"))
        with pytest.raises(ServiceError) as exc:
            MinerUProvider()._download_zip_markdown("https://cdn/r.zip", 60)
        assert exc.value.category == CATEGORY_HTTP
        assert exc.value.http_code == 403

    def test_download_zip_extracts_full_md(self, monkeypatch):
        body = _make_zip("# ok")
        monkeypatch.setattr(mineru_mod, "http_get",
                            lambda *a, **kw: (200, {}, body))
        assert MinerUProvider()._download_zip_markdown("https://cdn/r.zip", 60) == "# ok"


class TestMineruCredentials:
    def test_anonymous_reports_nothing_to_verify(self):
        out = MinerUProvider(_popts(mineru={})).test_credentials()
        assert "anonymous" in out

    def test_v4_token_rejected_401(self, monkeypatch):
        def raise_401(*a, **kw):
            raise ServiceError("HTTP 401", CATEGORY_HTTP, http_code=401)
        monkeypatch.setattr(mineru_mod, "http_get", raise_401)
        with pytest.raises(CredentialsError):
            MinerUProvider(_popts(mineru={"api_key": "bad"})).test_credentials()

    def test_v4_token_accepted_on_probe_miss(self, monkeypatch):
        # 探测 batch 不存在（4xx 但非 401）→ 鉴权通过
        def raise_404(*a, **kw):
            raise ServiceError("HTTP 404", CATEGORY_HTTP, http_code=404)
        monkeypatch.setattr(mineru_mod, "http_get", raise_404)
        out = MinerUProvider(_popts(mineru={"api_key": "good"})).test_credentials()
        assert "token accepted" in out


# ════════════════════════════════════════════════════════════════════════════
# doubao
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers import doubao as doubao_mod
from eztool.providers.doubao import DoubaoProvider, _pick_auth


class TestDoubaoPickAuth:
    def test_auth_choice_priority(self):
        # api_key 优先于 ak+sk；无 key 时用 ak+sk；auth 显式指定最优先
        assert _pick_auth({"api_key": "k", "ak": "a", "sk": "s"}) == "apikey"
        assert _pick_auth({"ak": "a", "sk": "s"}) == "aksk"
        assert _pick_auth({"auth": "aksk", "api_key": "k", "ak": "a", "sk": "s"}) == "aksk"

    def test_missing_or_invalid_auth_raises(self):
        with pytest.raises(CredentialsError):
            _pick_auth({})
        with pytest.raises(CredentialsError):
            _pick_auth({"auth": "oauth"})


class TestDoubaoSearch:
    def _run(self, monkeypatch, item):
        captured = {}

        def fake_post_json(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload)
            body = {"Result": {"WebResults": [item], "SearchTime": 123},
                    "ResponseMetadata": {"RequestId": "r1"}}
            return 200, {}, json.dumps(body).encode("utf-8")

        monkeypatch.setattr(doubao_mod, "post_json", fake_post_json)
        svc = DoubaoProvider(_popts(doubao={"api_key": "k"}))
        resp = svc.search("web", "q", {})
        return resp, captured

    def test_web_mode(self, monkeypatch):
        resp, cap = self._run(monkeypatch, {
            "Title": "t", "Url": "https://a/", "Summary": "snip",
        })
        assert cap["payload"]["SearchType"] == "web"
        assert cap["headers"]["Authorization"] == "Bearer k"
        assert resp.results[0].snippet == "snip"
        assert resp.metadata["request_id"] == "r1"

    def test_missing_credentials_raises(self):
        svc = DoubaoProvider(_popts(doubao={}))
        with pytest.raises(CredentialsError):
            svc.search("web", "q", {})


# ════════════════════════════════════════════════════════════════════════════
# anysearch
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers.anysearch import AnySearchProvider


class TestAnySearch:
    def test_web_categories_and_no_default_chain_priority_for_page(self):
        # web 搜索 + page 提取（--use 显式用）；不再声明 data 类别与 page priority
        assert AnySearchProvider.categories == frozenset({"web", "page"})
        assert AnySearchProvider.priority == {"web": 30}


# ════════════════════════════════════════════════════════════════════════════
# keen
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers import keen as keen_mod
from eztool.providers.keen import KeenProvider
from eztool.util import NoResultsError

KEEN_ITEM = {
    "title": "TS Best Practices",
    "url": "https://example.com/ts",
    "description": "short summary",
    "snippet": "longer excerpt from the page",
    "published_at": "2026-01-15T10:30:00Z",
}


def _keen_search(monkeypatch, configs, payload):
    captured = {}

    def fake_post_json(url, headers, body, timeout):
        captured.update(url=url, headers=headers, body=body)
        return 200, {}, json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(keen_mod, "post_json", fake_post_json)
    resp = KeenProvider(_popts(keen=configs)).search("web", "q", {})
    return resp, captured


class TestKeenSearch:
    def test_keyless_uses_public_path_and_title_header(self, monkeypatch):
        resp, cap = _keen_search(monkeypatch, {}, {"query": "q", "results": [KEEN_ITEM]})
        assert cap["url"].endswith("/v1/search/public")
        assert cap["headers"]["X-Keenable-Title"] == "eztool"
        assert "X-API-Key" not in cap["headers"]
        assert cap["body"] == {"query": "q"}
        r = resp.results[0]
        assert r.snippet == "short summary"
        assert r.content == "longer excerpt from the page"
        assert r.extra == {"published_at": "2026-01-15T10:30:00Z"}

    def test_api_key_uses_authed_path(self, monkeypatch):
        _keen_search(monkeypatch, {"api_key": "keen_k"},
                     {"query": "q", "results": [KEEN_ITEM]})
        resp, cap = _keen_search(monkeypatch, {"api_key": "keen_k"},
                                 {"query": "q", "results": [KEEN_ITEM]})
        assert cap["url"].endswith("/v1/search")
        assert cap["headers"]["X-API-Key"] == "keen_k"

    def test_no_results_raises(self, monkeypatch):
        with pytest.raises(NoResultsError):
            _keen_search(monkeypatch, {}, {"query": "q", "results": []})

    def test_has_credentials_always_true(self):
        assert KeenProvider().has_credentials() is True


class TestKeenFetch:
    def test_fetch_parses_markdown_content(self, monkeypatch):
        rec = _Recorder([json_response({"url": "https://a/", "content": "# hello"})])
        monkeypatch.setattr("urllib.request.urlopen", rec)
        result = KeenProvider().fetch("https://a/", timeout=30)
        assert result.content == "# hello"
        req = rec.seen[0]
        assert "/v1/fetch/public?" in req.full_url
        assert "url=https%3A%2F%2Fa%2F" in req.full_url
        assert "live=true" in req.full_url
        assert req.get_header("X-keenable-title") == "eztool"

    def test_fetch_with_key_uses_authed_path(self, monkeypatch):
        rec = _Recorder([json_response({"content": "# hi"})])
        monkeypatch.setattr("urllib.request.urlopen", rec)
        KeenProvider(_popts(keen={"api_key": "keen_k"})).fetch("https://a/", timeout=30)
        req = rec.seen[0]
        assert "/v1/fetch?" in req.full_url
        assert req.get_header("X-api-key") == "keen_k"

    def test_fetch_empty_content_raises(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            _Recorder([json_response({"content": ""})]))
        with pytest.raises(ServiceError) as exc:
            KeenProvider().fetch("https://a/", timeout=30)
        assert exc.value.category == CATEGORY_HTTP


# ════════════════════════════════════════════════════════════════════════════
# parallel
# ════════════════════════════════════════════════════════════════════════════

from eztool.providers import parallel as parallel_mod
from eztool.providers.parallel import ParallelProvider

PARALLEL_ITEM = {
    "url": "https://example.com/p",
    "title": "Parallel page",
    "publish_date": "2024-01-15",
    "excerpts": ["excerpt one", "excerpt two"],
}


def _parallel_post(monkeypatch, payload):
    captured = {}

    def fake_post_json(url, headers, body, timeout):
        captured.update(url=url, headers=headers, body=body)
        return 200, {}, json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(parallel_mod, "post_json", fake_post_json)
    return captured


class TestParallelSearch:
    def test_search_payload_and_mapping(self, monkeypatch):
        cap = _parallel_post(monkeypatch, {
            "search_id": "search_x",
            "results": [PARALLEL_ITEM],
            "session_id": "session_x",
        })
        svc = ParallelProvider(_popts(parallel={"api_key": "pk"}))
        resp = svc.search("web", "what is parallel", {"count": 5})
        assert cap["url"].endswith("/v1/search")
        assert cap["headers"]["x-api-key"] == "pk"
        assert cap["body"]["objective"] == "what is parallel"
        assert cap["body"]["search_queries"] == ["what is parallel"]
        assert cap["body"]["mode"] == "fast"
        assert cap["body"]["advanced_settings"] == {"max_results": 5}
        r = resp.results[0]
        assert r.snippet == "excerpt one\nexcerpt two"[:300]
        assert r.content == "excerpt one\n\nexcerpt two"
        assert r.extra == {"publish_date": "2024-01-15"}
        assert resp.metadata["search_id"] == "search_x"

    def test_missing_key_raises(self):
        svc = ParallelProvider(_popts(parallel={}))
        with pytest.raises(ServiceError):
            svc.search("web", "q", {})

    def test_no_results_raises(self, monkeypatch):
        _parallel_post(monkeypatch, {"search_id": "s", "results": [], "session_id": "s"})
        svc = ParallelProvider(_popts(parallel={"api_key": "pk"}))
        with pytest.raises(NoResultsError):
            svc.search("web", "q", {})


class TestParallelFetch:
    def test_fetch_returns_full_content(self, monkeypatch):
        cap = _parallel_post(monkeypatch, {
            "extract_id": "e1",
            "results": [{"url": "https://a/", "title": "t",
                         "excerpts": ["ex"], "full_content": "# full md"}],
            "errors": [],
            "session_id": "s1",
        })
        svc = ParallelProvider(_popts(parallel={"api_key": "pk"}))
        result = svc.fetch("https://a/", timeout=30)
        assert result.content == "# full md"
        assert cap["url"].endswith("/v1/extract")
        assert cap["body"]["urls"] == ["https://a/"]
        assert cap["body"]["advanced_settings"] == {"full_content": True}

    def test_fetch_falls_back_to_excerpts(self, monkeypatch):
        _parallel_post(monkeypatch, {
            "extract_id": "e1",
            "results": [{"url": "https://a/", "excerpts": ["ex1", "ex2"],
                         "full_content": None}],
            "errors": [],
            "session_id": "s1",
        })
        svc = ParallelProvider(_popts(parallel={"api_key": "pk"}))
        assert svc.fetch("https://a/", timeout=30).content == "ex1\n\nex2"

    def test_fetch_error_entries_raise_with_detail(self, monkeypatch):
        _parallel_post(monkeypatch, {
            "extract_id": "e1",
            "results": [],
            "errors": [{"url": "https://a/", "error_type": "fetch_error",
                        "http_status_code": 500, "content": "boom"}],
            "session_id": "s1",
        })
        svc = ParallelProvider(_popts(parallel={"api_key": "pk"}))
        with pytest.raises(ServiceError) as exc:
            svc.fetch("https://a/", timeout=30)
        assert "boom" in str(exc.value)

    def test_fetch_missing_key_raises(self):
        svc = ParallelProvider(_popts(parallel={}))
        with pytest.raises(ServiceError):
            svc.fetch("https://a/", timeout=30)
