"""--summarize：summarize 模块（配置/prompt/OpenAI 后端）+ api 钩子 + CLI 渲染。

全部 mock post_json / 假 provider，零真实网络。
"""

import json

import pytest

from eztool import api
from eztool import cli
from eztool import summarize as smm
from eztool.provider import FetchResult
from eztool.util import CATEGORY_HTTP, ServiceError, UsageError

from conftest import make_fetch_provider, make_search_provider

SUMMARIZE_CFG = {
    "backend": "openai",
    "base_url": "https://llm.test/v1",
    "api_key": "sk-test",
    "model": "test-model",
    "timeout": 10,
}


def _cfg(base_cfg, **extra):
    sec = dict(SUMMARIZE_CFG)
    sec.update(extra)
    return base_cfg(providers={}, chains={}) | {"summarize": sec}


def _openai_ok(content="synthesized answer [1]"):
    def fake_post_json(url, headers, body, timeout):
        return 200, {}, json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")
    return fake_post_json


# ════════════════════════════════════════════════════════════════════════════
# summarize 模块
# ════════════════════════════════════════════════════════════════════════════

class TestResolveConfig:
    def test_missing_required_keys_is_usage_error(self, base_cfg):
        cfg = base_cfg() | {"summarize": {"backend": "openai"}}
        with pytest.raises(UsageError) as exc:
            smm.resolve_config(cfg)
        for key in ("summarize.base_url", "summarize.api_key", "summarize.model"):
            assert key in str(exc.value)

    def test_unknown_backend_is_usage_error(self, base_cfg):
        cfg = _cfg(base_cfg, backend="nope")
        with pytest.raises(UsageError, match="nope"):
            smm.resolve_config(cfg)

    def test_valid_config_passes(self, base_cfg):
        assert smm.resolve_config(_cfg(base_cfg))["model"] == "test-model"


class TestBuildUserPrompt:
    def _items(self):
        return [
            smm.SourceItem(title="T1", url="https://a/", text="alpha body",
                           provider="keen"),
            smm.SourceItem(title="T2", url="https://b/", text="beta body"),
            smm.SourceItem(title="T3", url="https://c/", text="   "),  # 空文本跳过
        ]

    def test_numbering_request_and_provider_tag(self):
        user, citations = smm.build_user_prompt("my question", self._items())
        assert user.startswith("Request: my question")
        assert "[1] T1 — https://a/ (via keen)" in user
        assert "[2] T2 — https://b/" in user
        assert "T3" not in user  # 空内容不占编号
        assert [c.index for c in citations] == [1, 2]
        assert citations[0].provider == "keen"
        assert citations[1].url == "https://b/"


class TestOpenAISummarizer:
    def test_request_shape_and_auth(self, monkeypatch):
        captured = {}

        def fake_post_json(url, headers, body, timeout):
            captured.update(url=url, headers=headers, body=body, timeout=timeout)
            return 200, {}, json.dumps(
                {"choices": [{"message": {"content": "ok"}}]}
            ).encode("utf-8")

        monkeypatch.setattr(smm, "post_json", fake_post_json)
        out = smm.OpenAISummarizer().complete("sys", "usr", SUMMARIZE_CFG, 10)
        assert out == "ok"
        assert captured["url"] == "https://llm.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer sk-test"
        assert captured["body"]["model"] == "test-model"
        assert "max_tokens" not in captured["body"]
        assert captured["body"]["messages"][0] == {"role": "system", "content": "sys"}
        assert captured["timeout"] == 10

    def test_non_200_and_bad_json_raise(self, monkeypatch):
        monkeypatch.setattr(smm, "post_json",
                            lambda *a, **kw: (500, {}, b"server error"))
        with pytest.raises(ServiceError) as exc:
            smm.OpenAISummarizer().complete("s", "u", SUMMARIZE_CFG, 10)
        assert exc.value.category == CATEGORY_HTTP
        monkeypatch.setattr(smm, "post_json", lambda *a, **kw: (200, {}, b"not json"))
        with pytest.raises(ServiceError):
            smm.OpenAISummarizer().complete("s", "u", SUMMARIZE_CFG, 10)


class TestSummarizeEntry:
    def test_empty_items_raise(self, base_cfg):
        with pytest.raises(ServiceError, match="nothing to summarize"):
            smm.summarize(_cfg(base_cfg), "q",
                          [smm.SourceItem(title="t", url="u", text="  ")])

    def test_end_to_end(self, base_cfg, monkeypatch):
        monkeypatch.setattr(smm, "post_json", _openai_ok())
        summary = smm.summarize(
            _cfg(base_cfg), "q",
            [smm.SourceItem(title="T", url="https://a/", text="body",
                            provider="keen")])
        assert summary.answer == "synthesized answer [1]"
        assert summary.citations[0].provider == "keen"


# ════════════════════════════════════════════════════════════════════════════
# api 钩子：search --summarize / fetch_many / summarize_pages
# ════════════════════════════════════════════════════════════════════════════

class TestSearchSummarize:
    def test_answer_and_citations_attached(self, base_cfg, monkeypatch):
        make_search_provider("fake_s", results=[
            {"title": "T1", "url": "https://a/", "snippet": "snip",
             "content": "full body"},
        ])
        monkeypatch.setattr(smm, "post_json", _openai_ok())
        resp = api.search(_cfg(base_cfg), "web", "my query",
                          {"use": "fake_s", "summarize": True})
        assert resp.answer == "synthesized answer [1]"
        assert len(resp.citations) == 1
        assert resp.citations[0].provider == "fake_s"  # 链来源回填为引用标注
        assert resp.results  # 原始结果保留（由 CLI 决定是否展示）

    def test_llm_failure_degrades_to_raw(self, base_cfg, monkeypatch, capsys):
        make_search_provider("fake_s", results=[
            {"title": "T1", "url": "https://a/", "snippet": "snip"},
        ])
        monkeypatch.setattr(smm, "post_json",
                            lambda *a, **kw: (500, {}, b"boom"))
        resp = api.search(_cfg(base_cfg), "web", "q",
                          {"use": "fake_s", "summarize": True})
        assert resp.answer is None
        assert "summary_error" in resp.metadata
        assert resp.results  # 原始结果仍在


class TestFetchMany:
    def test_multiple_urls_in_input_order(self, base_cfg):
        make_fetch_provider("fake_p", content="page body")
        results, errors = api.fetch_many(
            base_cfg(), ["https://a/", "https://b/", "https://c/"],
            {"use": "fake_p"})
        assert errors == []
        assert [r.url for r in results] == ["https://a/", "https://b/", "https://c/"]

    def test_partial_failure_collected(self, base_cfg):
        from eztool.provider import Provider, register

        class _Flaky(Provider):
            name = "fake_flaky"
            categories = frozenset({"page"})

            def fetch(self, url, timeout=30):
                if "bad" in url:
                    raise ServiceError("boom", CATEGORY_HTTP)
                return FetchResult(provider=self.name, content="ok", url=url,
                                   elapsed=0.0)

        register(_Flaky)
        results, errors = api.fetch_many(
            base_cfg(), ["https://ok/", "https://bad/"], {"use": "fake_flaky"})
        assert [r.url for r in results] == ["https://ok/"]
        assert errors[0][0] == "https://bad/"

    def test_all_failed_raises(self, base_cfg):
        make_fetch_provider("fake_dead",
                            fail_with=ServiceError("boom", CATEGORY_HTTP))
        with pytest.raises(ServiceError):
            api.fetch_many(base_cfg(), ["https://a/"], {"use": "fake_dead"})


class TestSummarizePages:
    def test_citations_carry_provider(self, base_cfg, monkeypatch):
        monkeypatch.setattr(smm, "post_json", _openai_ok())
        results = [
            FetchResult(provider="keen", content="body a", url="https://a/",
                        elapsed=0.0),
            FetchResult(provider="tavily", content="body b", url="https://b/",
                        elapsed=0.0),
        ]
        summary = api.summarize_pages(_cfg(base_cfg), "focus question", results)
        assert [c.provider for c in summary.citations] == ["keen", "tavily"]
        assert [c.url for c in summary.citations] == ["https://a/", "https://b/"]


# ════════════════════════════════════════════════════════════════════════════
# CLI 渲染与参数面
# ════════════════════════════════════════════════════════════════════════════

def _set_summarize_config():
    for key, val in (("summarize.base_url", "https://llm.test/v1"),
                     ("summarize.api_key", "sk-test"),
                     ("summarize.model", "test-model")):
        cli.main(["config", "set", key, val])


class TestCliSummarize:
    def test_search_summarize_output(self, isolated_config, monkeypatch, capsys):
        make_search_provider("fake_s", results=[
            {"title": "T1", "url": "https://a/", "snippet": "snip"},
        ])
        monkeypatch.setattr(smm, "post_json", _openai_ok())
        _set_summarize_config()
        capsys.readouterr()
        cli.main(["search", "my query", "--use", "fake_s", "--summarize"])
        out = capsys.readouterr().out
        assert "## Summary: my query" in out
        assert "synthesized answer [1]" in out
        assert "### Sources" in out
        assert "[1] [T1](https://a/) **[fake_s]**" in out
        assert "### Results" not in out  # 总结替代原始列表

    def test_search_summarize_without_config_exit_2(self, isolated_config):
        make_search_provider("fake_s", results=[{"title": "t", "url": "u"}])
        with pytest.raises(SystemExit) as exc:
            cli.main(["search", "q", "--use", "fake_s", "--summarize"])
        assert exc.value.code == 2

    def test_image_summarize_rejected(self, isolated_config):
        with pytest.raises(SystemExit) as exc:
            cli.main(["search", "q", "--image", "--summarize"])
        assert exc.value.code == 2

    def test_fetch_multiple_urls_concat(self, isolated_config, capsys):
        make_fetch_provider("fake_p", content="page body")
        cli.main(["fetch", "https://a/", "https://b/", "--use", "fake_p"])
        out = capsys.readouterr().out
        assert out.count("page body") == 2
        assert "<!-- eztool: https://a/ [fake_p] -->" in out
        assert "<!-- eztool: https://b/ [fake_p] -->" in out

    def test_fetch_summarize_with_query(self, isolated_config, monkeypatch, capsys):
        make_fetch_provider("fake_p", content="page body")
        captured = {}

        def fake_post_json(url, headers, body, timeout):
            captured.update(body=body)
            return 200, {}, json.dumps(
                {"choices": [{"message": {"content": "page answer"}}]}
            ).encode("utf-8")

        monkeypatch.setattr(smm, "post_json", fake_post_json)
        _set_summarize_config()
        capsys.readouterr()
        cli.main(["fetch", "https://a/", "--use", "fake_p",
                  "--summarize", "--query", "only pricing"])
        out = capsys.readouterr().out
        assert "page answer" in out
        assert "### Sources" in out
        prompt = captured["body"]["messages"][1]["content"]
        assert "Request: only pricing" in prompt
        assert "page body" in prompt

    def test_fetch_summarize_llm_failure_falls_back(self, isolated_config,
                                                    monkeypatch, capsys):
        make_fetch_provider("fake_p", content="page body")
        monkeypatch.setattr(smm, "post_json", lambda *a, **kw: (500, {}, b"boom"))
        _set_summarize_config()
        capsys.readouterr()
        cli.main(["fetch", "https://a/", "--use", "fake_p", "--summarize"])
        res = capsys.readouterr()
        assert "warning: summarize failed" in res.err
        assert "page body" in res.out  # 降级输出原始内容

    def test_convert_summarize(self, isolated_config, monkeypatch, capsys, tmp_path):
        make_fetch_provider("fake_c", content="doc body",
                            categories=("file",))
        monkeypatch.setattr(smm, "post_json", _openai_ok("doc answer"))
        _set_summarize_config()
        f = tmp_path / "a.txt"
        f.write_text("x")
        capsys.readouterr()
        cli.main(["convert", str(f), "--use", "fake_c", "--summarize"])
        out = capsys.readouterr().out
        assert "doc answer" in out
