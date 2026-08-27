"""执行语义：search 回退链 / 并行合并、fetch/convert 覆盖链、timeout 解析、api 层质量门。

全部用假 provider + 隔离配置，零网络。假 provider 工厂见 conftest.py。
"""

import pytest

from eztool import api
from eztool.util import (
    CATEGORY_HTTP,
    CredentialsError,
    ServiceError,
    UsageError,
)

from conftest import make_fetch_provider, make_search_provider

OK = [{"title": "t1", "url": "https://a/1"}]
BOOM = ServiceError("boom", CATEGORY_HTTP)


# ── search 回退链 ────────────────────────────────────────────────────────────


class TestSearchChain:
    def test_first_success_wins(self, base_cfg):
        a = make_search_provider("ch_a", results=OK)
        b = make_search_provider("ch_b", results=[{"title": "t2", "url": "u2"}])
        cfg = base_cfg(chains={"web": ["ch_a", "ch_b"]})
        resp = api.search(cfg, "web", "q")
        assert resp.metadata["backend"] == "ch_a"
        assert len(a.calls) == 1 and len(b.calls) == 0  # 短路：b 未被调用

    def test_failure_falls_through(self, base_cfg):
        a = make_search_provider("ch_a", fail_with=BOOM)
        b = make_search_provider("ch_b", results=OK)
        cfg = base_cfg(chains={"web": ["ch_a", "ch_b"]})
        resp = api.search(cfg, "web", "q")
        assert resp.metadata["backend"] == "ch_b"
        assert len(a.calls) == 1 and len(b.calls) == 1

    def test_all_failed_raises_search_failed(self, base_cfg):
        make_search_provider("ch_a", fail_with=BOOM)
        make_search_provider("ch_b", fail_with=BOOM)
        cfg = base_cfg(chains={"web": ["ch_a", "ch_b"]})
        with pytest.raises(ServiceError) as exc:
            api.search(cfg, "web", "q")
        assert exc.value.code == "search_failed"

    def test_auth_required_without_credentials_skipped(self, base_cfg):
        locked = make_search_provider("ch_lock", auth_required=True,
                                      credentialed=False)
        open_ = make_search_provider("ch_open", results=OK)
        cfg = base_cfg(chains={"web": ["ch_lock", "ch_open"]})
        resp = api.search(cfg, "web", "q")
        assert resp.metadata["backend"] == "ch_open"
        assert len(locked.calls) == 0  # 未配凭证 → 直接跳过，不发起调用

    def test_explicit_use_without_credentials_raises(self, base_cfg):
        make_search_provider("ch_lock", auth_required=True, credentialed=False)
        with pytest.raises(CredentialsError):
            api.search(base_cfg(), "web", "q", {"use": "ch_lock"})

    def test_unknown_provider_is_usage_error(self, base_cfg):
        # --use 点名未知 provider = 用法错误（硬停）
        with pytest.raises(UsageError):
            api.search(base_cfg(), "web", "q", {"use": "nope"})

    def test_unknown_opt_keys_hard_error(self, base_cfg):
        # opts 只认保留键：未知键硬报错，不留静默透传通道
        make_search_provider("ch_a", results=OK)
        cfg = base_cfg(chains={"web": ["ch_a"]})
        with pytest.raises(UsageError) as exc:
            api.search(cfg, "web", "q", {"banana": 1, "timeout": None})
        assert "banana" in str(exc.value)

    def test_stale_names_in_config_chain_warn_and_filter(self, base_cfg, capsys):
        # 配置链残留已删除的 provider 名（旧配置升级）→ 警告后剔除，不硬停
        a = make_search_provider("ch_a", results=OK)
        cfg = base_cfg(chains={"web": ["deepseek", "ch_a"]})
        resp = api.search(cfg, "web", "q")
        assert resp.metadata["backend"] == "ch_a"
        assert len(a.calls) == 1
        assert "unknown providers" in capsys.readouterr().err


# ── search 并行（--use 多名）/ --max 升级链 ─────────────────────────────────


class TestSearchParallel:
    def test_use_merges_dedups_and_tags_source(self, base_cfg):
        make_search_provider("pa", results=[{"title": "a1", "url": "u1"},
                                            {"title": "dup-a", "url": "same"}])
        make_search_provider("pb", results=[{"title": "b1", "url": "u2"},
                                            {"title": "dup-b", "url": "same"}])
        resp = api.search(base_cfg(), "web", "q", {"use": "pa,pb"})
        # 公平轮转：pa 出一条、pb 出一条……首见 URL 入选，重复的丢弃
        assert [r.url for r in resp.results] == ["u1", "u2", "same"]
        assert {r.source for r in resp.results} <= {"pa", "pb"}
        assert all(r.source for r in resp.results)  # 每条都标注来源
        assert resp.metadata["backend"] == "pa,pb"

    def test_merged_capped_at_hard_limit(self, base_cfg, capsys):
        n = 30  # 两家各 30 条 = 60 候选 > 安全阀 40
        make_search_provider("pa", results=[{"title": f"a{i}", "url": f"ua{i}"}
                                            for i in range(n)])
        make_search_provider("pb", results=[{"title": f"b{i}", "url": f"ub{i}"}
                                            for i in range(n)])
        resp = api.search(base_cfg(), "web", "q", {"use": "pa,pb"})
        assert len(resp.results) == api.MERGED_HARD_CAP
        assert resp.metadata["truncated"] is True
        expected = [f"{p}{i}" for i in range(api.MERGED_HARD_CAP // 2)
                    for p in ("ua", "ub")]
        assert [r.url for r in resp.results] == expected  # 轮转且确定性
        assert "capped at" in capsys.readouterr().err

    def test_partial_failure_still_returns(self, base_cfg):
        make_search_provider("pa", fail_with=BOOM)
        make_search_provider("pb", results=OK)
        resp = api.search(base_cfg(), "web", "q", {"use": "pa,pb"})
        assert resp.metadata["backend"] == "pb"
        assert [r.url for r in resp.results] == ["https://a/1"]

    def test_parallel_all_failed(self, base_cfg):
        make_search_provider("pa", fail_with=BOOM)
        make_search_provider("pb", fail_with=BOOM)
        with pytest.raises(ServiceError) as exc:
            api.search(base_cfg(), "web", "q", {"use": "pa,pb"})
        assert exc.value.code == "search_failed"


class TestSearchEscalate:
    """--max N：逐家升级，去重累计达标即停，最后一家自然超出不修剪。"""

    def test_target_met_by_first_provider_no_more_calls(self, base_cfg):
        a = make_search_provider("ea", results=[
            {"title": f"t{i}", "url": f"u{i}"} for i in range(5)])
        b = make_search_provider("eb", results=[{"title": "x", "url": "ux"}])
        cfg = base_cfg(chains={"web": ["ea", "eb"]})
        resp = api.search(cfg, "web", "q", {"max": 3})
        assert len(a.calls) == 1 and len(b.calls) == 0  # 达标即停：b 未被问
        assert resp.metadata["backend"] == "ea"
        assert len(resp.results) == 5  # 超出 3 的部分不修剪

    def test_escalates_until_target_with_cross_dedup(self, base_cfg):
        make_search_provider("ea", results=[{"title": "t1", "url": "u1"},
                                            {"title": "t2", "url": "u2"}])
        make_search_provider("eb", results=[{"title": "dup", "url": "u1"},  # 与 ea 重叠
                                            {"title": "t3", "url": "u3"},
                                            {"title": "t4", "url": "u4"},
                                            {"title": "t5", "url": "u5"}])
        cfg = base_cfg(chains={"web": ["ea", "eb"]})
        resp = api.search(cfg, "web", "q", {"max": 5})
        assert {r.url for r in resp.results} == {"u1", "u2", "u3", "u4", "u5"}
        assert resp.metadata["backend"] == "ea,eb"

    def test_failure_falls_through_to_next(self, base_cfg):
        a = make_search_provider("ea", fail_with=BOOM)
        b = make_search_provider("eb", results=OK)
        cfg = base_cfg(chains={"web": ["ea", "eb"]})
        resp = api.search(cfg, "web", "q", {"max": 1})
        assert len(a.calls) == 1 and len(b.calls) == 1
        assert resp.metadata["backend"] == "eb"
        assert [r.url for r in resp.results] == ["https://a/1"]

    def test_names_exhausted_returns_partial(self, base_cfg):
        make_search_provider("ea", results=[{"title": "t1", "url": "u1"},
                                            {"title": "t2", "url": "u2"}])
        make_search_provider("eb", fail_with=BOOM)
        cfg = base_cfg(chains={"web": ["ea", "eb"]})
        resp = api.search(cfg, "web", "q", {"max": 10})
        assert len(resp.results) == 2  # 凑不满也带回已有结果
        assert resp.metadata["backend"] == "ea"

    @pytest.mark.parametrize("bad", [0, -3])
    def test_non_positive_max_is_usage_error(self, base_cfg, bad):
        with pytest.raises(UsageError):
            api.search(base_cfg(), "web", "q", {"max": bad})


# ── fetch / convert 顺序覆盖链 ───────────────────────────────────────────────


class TestConvertChain:
    def test_use_overrides_in_order(self, base_cfg, tmp_path):
        a = make_fetch_provider("fa", fail_with=BOOM)
        b = make_fetch_provider("fb", content="# from b")
        result = api.fetch(base_cfg(), "https://x.test/", {"use": "fa,fb"})
        assert result.provider == "fb" and result.content == "# from b"
        assert len(a.calls) == 1 and len(b.calls) == 1  # a 先被调用且失败

    def test_config_chain_default(self, base_cfg):
        a = make_fetch_provider("fa", content="# from a")
        b = make_fetch_provider("fb", content="# from b")
        cfg = base_cfg(chains={"page": ["fa", "fb"]})
        result = api.fetch(cfg, "https://x.test/")
        assert result.provider == "fa"
        assert len(b.calls) == 0

    def test_convert_missing_file_is_usage_error(self, base_cfg, tmp_path):
        with pytest.raises(UsageError):
            api.convert(base_cfg(), str(tmp_path / "missing.pdf"))

    def test_convert_uses_file_chain(self, base_cfg, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hi", encoding="utf-8")
        svc = make_fetch_provider("fc", content="# converted",
                                  categories=("file",))
        cfg = base_cfg(chains={"file": ["fc"]})
        result = api.convert(cfg, str(f))
        assert result.provider == "fc"
        assert svc.calls[0]["kind"] == "convert_file"


# ── timeout 解析：--timeout > providers.<name>.timeout > settings.timeout ────


class TestTimeoutResolution:
    def test_provider_timeout_beats_settings(self, base_cfg):
        svc = make_search_provider("t_a", results=OK)
        cfg = base_cfg(chains={"web": ["t_a"]},
                       providers={"t_a": {"timeout": 55}},
                       settings_timeout=30)
        api.search(cfg, "web", "q")
        assert svc.calls[0]["timeout"] == 55

    def test_cli_timeout_beats_provider(self, base_cfg):
        svc = make_search_provider("t_a", results=OK)
        cfg = base_cfg(chains={"web": ["t_a"]},
                       providers={"t_a": {"timeout": 55}},
                       settings_timeout=30)
        api.search(cfg, "web", "q", {"timeout": 99})
        assert svc.calls[0]["timeout"] == 99

    def test_settings_timeout_is_fallback(self, base_cfg):
        svc = make_fetch_provider("t_b", content="ok")
        cfg = base_cfg(chains={"page": ["t_b"]}, settings_timeout=17)
        api.fetch(cfg, "https://x.test/")
        assert svc.calls[0]["timeout"] == 17

    def test_unconfigured_web_provider_gets_fast_default(self, base_cfg):
        # 无任何显式超时：web 类别落到快速缺省（搜索不等长超时）
        svc = make_search_provider("t_fast", results=OK)
        cfg = base_cfg(chains={"web": ["t_fast"]}, settings_timeout=30)
        api.search(cfg, "web", "q")
        assert svc.calls[0]["timeout"] == api.SEARCH_DEFAULT_TIMEOUT

    def test_cli_wires_explicit_settings_through(self, isolated_config):
        # 端到端：稀疏文件里有 settings.timeout → cmd_search 关掉快速缺省
        import json

        from eztool import cli as ezcli
        svc = make_search_provider("t_cli", results=OK)
        (isolated_config / "config.json").write_text(
            json.dumps({"settings": {"timeout": 45}}), encoding="utf-8")
        ezcli.main(["search", "q", "--use", "t_cli"])
        assert svc.calls[0]["timeout"] == 45


# ── api 层质量门（_convert_chain）────────────────────────────────────────────

BLOCKED = "## 环境异常\n\n当前环境异常，完成验证后即可继续访问。\n\n去验证"
SUSPICIOUS = "# 环境异常提示\n\n" + ("一些正文内容。" * 200)  # ~1200 chars ∈ [800,1500)
GOOD = "# 正常文章\n\n" + ("正文内容充足。" * 200)


class TestQualityGate:
    def test_blocked_content_falls_through(self, base_cfg):
        bad = make_fetch_provider("q_bad", content=BLOCKED)
        good = make_fetch_provider("q_good", content=GOOD)
        cfg = base_cfg(chains={"page": ["q_bad", "q_good"]})
        result = api.fetch(cfg, "https://x.test/")
        assert result.provider == "q_good"
        assert len(bad.calls) == 1 and len(good.calls) == 1

    def test_suspicious_kept_as_backup_then_discarded(self, base_cfg):
        sus = make_fetch_provider("q_sus", content=SUSPICIOUS)
        good = make_fetch_provider("q_good", content=GOOD)
        cfg = base_cfg(chains={"page": ["q_sus", "q_good"]})
        result = api.fetch(cfg, "https://x.test/")
        assert result.provider == "q_good"  # 正常结果优于 backup

    def test_all_suspicious_returns_backup_with_warning(self, base_cfg, capsys):
        make_fetch_provider("q_sus1", content=SUSPICIOUS)
        make_fetch_provider("q_sus2", content=SUSPICIOUS)
        cfg = base_cfg(chains={"page": ["q_sus1", "q_sus2"]})
        result = api.fetch(cfg, "https://x.test/")
        assert result.provider == "q_sus1"  # 第一个 backup 兜底
        assert "warning" in capsys.readouterr().err

    def test_all_blocked_is_convert_failed(self, base_cfg):
        make_fetch_provider("q_bad1", content=BLOCKED)
        make_fetch_provider("q_bad2", content=BLOCKED)
        cfg = base_cfg(chains={"page": ["q_bad1", "q_bad2"]})
        with pytest.raises(ServiceError) as exc:
            api.fetch(cfg, "https://x.test/")
        assert exc.value.code == "convert_failed"
