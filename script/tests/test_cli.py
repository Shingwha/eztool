"""CLI 命令面：argparse 结构、互斥组、入口校验、main() 退出码、config show 脱敏。"""

import pytest

from eztool import cli
from eztool.util import CATEGORY_HTTP, ServiceError

from conftest import make_search_provider


def parse(argv):
    return cli.build_parser().parse_args(argv)


class TestParserStructure:
    def test_five_subcommands_exist(self):
        # 用 func 默认值验证子命令注册（--help 会触发 SystemExit，故不用它探测）
        assert parse(["search", "q"]).func is cli.cmd_search
        assert parse(["sources"]).func is cli.cmd_sources
        assert parse(["fetch", "https://x/"]).func is cli.cmd_fetch
        assert parse(["convert", "a.txt"]).func is cli.cmd_convert

    def test_config_six_actions(self):
        assert parse(["config", "show"]).func is cli.cmd_config_show
        assert parse(["config", "set", "k", "v"]).func is cli.cmd_config_set
        assert parse(["config", "get", "k"]).func is cli.cmd_config_get
        assert parse(["config", "reset", "k"]).func is cli.cmd_config_reset
        assert parse(["config", "test"]).func is cli.cmd_config_test
        assert parse(["config", "clear"]).func is cli.cmd_config_clear

    def test_search_mode_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            parse(["search", "q", "--image", "--source", "finance.quote"])

    def test_search_breadth_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            parse(["search", "q", "--all", "--use", "doubao"])

    def test_image_params_present(self):
        args = parse(["search", "q", "--image", "--width-min", "100",
                      "--height-max", "200", "--shapes", "方形"])
        assert args.image is True
        assert args.width_min == 100 and args.height_max == 200
        assert args.shapes == "方形"


class TestEntryValidation:
    """fetch/convert 的目标类型校验：走 main() 断言退出码 2。"""

    def test_fetch_rejects_local_path(self, isolated_config, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["fetch", "README.md"])
        assert exc.value.code == 2
        assert "convert" in capsys.readouterr().err

    def test_convert_rejects_url(self, isolated_config, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["convert", "https://example.com/a.pdf"])
        assert exc.value.code == 2
        assert "fetch" in capsys.readouterr().err

    def test_fetch_missing_target(self, isolated_config):
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
