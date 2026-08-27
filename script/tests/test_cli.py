"""CLI 命令面：argparse 结构、互斥组、入口校验、main() 退出码、config show 脱敏。"""

import json

import pytest

from eztool import cli
from eztool.util import CATEGORY_HTTP, ServiceError

from conftest import make_search_provider


def parse(argv):
    return cli.build_parser().parse_args(argv)


class TestParserStructure:
    def test_subcommand_dispatch_map(self):
        assert parse(["search", "q"]).func is cli.cmd_search
        assert parse(["fetch", "https://x/"]).func is cli.cmd_fetch
        assert parse(["convert", "a.txt"]).func is cli.cmd_convert
        cases = [("show", [], cli.cmd_config_show), ("set", ["k"], cli.cmd_config_set),
                 ("get", ["k"], cli.cmd_config_get),
                 ("reset", ["k"], cli.cmd_config_reset),
                 ("test", [], cli.cmd_config_test), ("clear", [], cli.cmd_config_clear)]
        for verb, extra, fn in cases:
            assert parse(["config", verb, *extra]).func is fn

    def test_removed_flags_stay_gone(self):
        # v0.5/v0.6 陆续删除的旗标：保持未知参数（exit 2），防止复活
        for argv in (["search", "q", "--all"], ["search", "q", "--count", "10"],
                     ["search", "q", "--image"], ["search", "q", "--source", "f"]):
            with pytest.raises(SystemExit):
                parse(argv)

    def test_new_search_flags_parse(self):
        args = parse(["search", "q", "--use", "doubao,keen", "--max", "12",
                      "--out", "x.md"])
        assert (args.use, args.max, args.out) == ("doubao,keen", 12, "x.md")


class TestEntryValidation:
    """fetch/convert 的目标类型校验：走 main() 断言退出码 2 与 stderr 提示。"""

    def test_target_validation(self, isolated_config, capsys):
        cases = [
            (["fetch", "README.md"], "convert"),          # 本地路径 → 该走 convert
            (["convert", "https://example.com/a.pdf"], "fetch"),
        ]
        for argv, hint in cases:
            with pytest.raises(SystemExit) as exc:
                cli.main(argv)
            assert exc.value.code == 2
            assert hint in capsys.readouterr().err
        with pytest.raises(SystemExit) as exc:
            cli.main(["fetch"])
        assert exc.value.code == 2


class TestMainExitCodes:
    def test_usage_error_exit_2(self, isolated_config):
        with pytest.raises(SystemExit) as exc:
            cli.main(["search", "q", "--use", "nope"])
        assert exc.value.code == 2

    def test_service_error_exit_1(self, isolated_config, capsys):
        make_search_provider("fake_fail",
                             fail_with=ServiceError("boom", CATEGORY_HTTP))
        with pytest.raises(SystemExit) as exc:
            cli.main(["search", "q", "--use", "fake_fail"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "error:" in err and "search_failed" in err

    def test_keyboard_interrupt_exit_130(self, isolated_config):
        make_search_provider("fake_kbd", fail_with=KeyboardInterrupt())
        with pytest.raises(SystemExit) as exc:
            cli.main(["search", "q", "--use", "fake_kbd"])
        assert exc.value.code == 130


class TestSearchOutEmit:
    """search --out：结果写入文件，stdout 只留一行确认（与 fetch/convert 对齐）。"""

    def test_out_writes_file_and_prints_note(self, isolated_config, capsys,
                                             tmp_path, monkeypatch):
        from eztool.provider import SearchResponse, SearchResult

        canned = SearchResponse(
            query="q",
            results=[SearchResult(title="T1", url="https://a/", snippet="snip")],
            metadata={"backend": "fake_s"},
        )
        monkeypatch.setattr(cli.api, "search", lambda *a, **k: canned)
        out_path = tmp_path / "results.md"
        cli.main(["search", "q", "--use", "unused", "--out", str(out_path)])
        text = out_path.read_text(encoding="utf-8")
        assert "## Search Results: q" in text
        assert "[T1](https://a/)" in text
        assert f"wrote {out_path}" in capsys.readouterr().out

    def test_out_with_summarize_writes_summary(self, isolated_config, capsys,
                                               tmp_path, monkeypatch):
        from eztool import summarize as smm
        from eztool.provider import SearchResponse

        make_search_provider("fake_sum",
                             results=[{"title": "T", "url": "u",
                                       "snippet": "usable text"}])
        monkeypatch.setattr(smm, "post_json", lambda *a, **kw: (
            200, {}, json.dumps(
                {"choices": [{"message": {"content": "the answer"}}]}
            ).encode("utf-8")))
        for key, val in (("summarize.base_url", "https://llm.test/v1"),
                         ("summarize.api_key", "sk-test"),
                         ("summarize.model", "test-model")):
            cli.main(["config", "set", key, val])
        capsys.readouterr()
        out_path = tmp_path / "summary.md"
        cli.main(["search", "q", "--use", "fake_sum", "--summarize",
                  "--out", str(out_path)])
        text = out_path.read_text(encoding="utf-8")
        assert "## Summary:" in text and "the answer" in text
        assert f"wrote {out_path}" in capsys.readouterr().out


class TestConfigShow:
    def test_show_has_three_sections_and_masks_secrets(self, isolated_config, capsys):
        cli.main(["config", "set", "providers.doubao.api_key", "abcd1234wxyz"])
        capsys.readouterr()  # 丢掉 set 的输出
        cli.main(["config", "show"])
        out = capsys.readouterr().out
        assert "settings.timeout = 30" in out
        assert "chains.web = " in out
        assert "providers.doubao.api_key = abcd****wxyz" in out
        assert "abcd1234wxyz" not in out  # 原始 secret 不泄露

    def test_get_masks_secret_and_set_unknown_key(self, isolated_config, capsys):
        cli.main(["config", "set", "providers.doubao.api_key", "abcd1234wxyz"])
        capsys.readouterr()
        cli.main(["config", "get", "providers.doubao.api_key"])
        assert capsys.readouterr().out.strip() == "abcd****wxyz"
        with pytest.raises(SystemExit) as exc:
            cli.main(["config", "set", "no.such.key", "v"])
        assert exc.value.code == 2


class TestSparseConfigFile:
    """config set/reset 只在覆盖值上增删：文件稀疏，不携带默认值。"""

    def test_set_writes_only_that_key(self, isolated_config):
        cli.main(["config", "set", "providers.tavily.api_key", "tvly-x"])
        data = json.loads((isolated_config / "config.json").read_text(encoding="utf-8"))
        assert data == {"providers": {"tavily": {"api_key": "tvly-x"}}}

    def test_reset_semantics(self, isolated_config, capsys):
        # reset 已设键 = 从稀疏文件删除该键；reset 未设键 = 纯 no-op 不建文件
        cli.main(["config", "set", "chains.web", "doubao"])
        cli.main(["config", "reset", "chains.web"])
        capsys.readouterr()
        data = json.loads((isolated_config / "config.json").read_text(encoding="utf-8"))
        assert data == {}  # 空段也被清掉
        cli.main(["config", "show"])
        assert "chains.web = " in capsys.readouterr().out  # 合并视图仍有默认链
        # 无文件的目录里 reset 未设键 = 纯 no-op，连文件都不建
        (isolated_config / "config.json").unlink()
        cli.main(["config", "reset", "settings.timeout"])
        out = capsys.readouterr().out
        assert "reset settings.timeout = 30" in out
        assert not (isolated_config / "config.json").exists()
