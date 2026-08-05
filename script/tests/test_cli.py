"""CLI 命令树测试：域 + 子命令自动生成、参数面按类别定制、无旧参数残留。"""

import argparse
import io
import unittest
from unittest import mock

from ezwork_tool import api, cli
from ezwork_tool.base import SearchResponse, SearchResult


def _subparsers(parser: argparse.ArgumentParser) -> dict:
    """取子命令 action 的 {name: parser} 映射。"""
    return parser._subparsers._group_actions[0].choices


def _args(parser: argparse.ArgumentParser) -> set:
    return {a.dest for a in parser._actions if a.dest != "help"}


class TestCommandTree(unittest.TestCase):
    def setUp(self):
        self.p = cli.build_parser()
        self.subs = _subparsers(self.p)

    def test_top_level_has_core_domains(self):
        self.assertLessEqual({"search", "convert", "config"}, set(self.subs))

    def test_search_subcommands_include_core(self):
        ssubs = _subparsers(self.subs["search"])
        self.assertLessEqual({"web", "image", "data", "tags"}, set(ssubs))

    def test_web_params(self):
        """search web 只有全局参数（provider 无特有参数，与既有 provider 一致）。"""
        web = _subparsers(self.subs["search"])["web"]
        self.assertLessEqual({"query", "providers", "count", "timeout"}, _args(web))

    def test_image_params_include_provider_specific(self):
        image = _subparsers(self.subs["search"])["image"]
        self.assertLessEqual({"query", "providers", "count", "timeout",
                              "width_min", "width_max", "height_min",
                              "height_max", "shapes"}, _args(image))

    def test_data_params_include_tag(self):
        data = _subparsers(self.subs["search"])["data"]
        self.assertIn("tag", _args(data))
        self.assertIn("params", _args(data))
        self.assertNotIn("full", _args(data))

    def test_convert_params(self):
        conv = self.subs["convert"]
        self.assertLessEqual(
            {"target", "out", "providers", "timeout", "list_providers"}, _args(conv))

    def test_config_subcommands(self):
        csubs = _subparsers(self.subs["config"])
        self.assertLessEqual({"show", "set", "get", "reset", "test", "clear"},
                             set(csubs))
        test_args = _args(csubs["test"])
        self.assertIn("providers", test_args)
        self.assertNotIn("backend", test_args)

    def test_no_legacy_flags_anywhere(self):
        """验收标准 6：无 --backend / --image / --paper 残留。"""
        with self.assertRaises(SystemExit):
            self.p.parse_args(["search", "web", "q", "--backend", "x"])
        with self.assertRaises(SystemExit):
            self.p.parse_args(["search", "web", "q", "--image"])
        with self.assertRaises(SystemExit):
            self.p.parse_args(["paper", "q"])

    def test_parse_image_subcommand(self):
        ns = self.p.parse_args(["search", "image", "猫", "--width-min", "100", "--shapes", "方形"])
        self.assertEqual(ns.category, "search.image")
        self.assertEqual(ns.width_min, 100)
        self.assertEqual(ns.shapes, "方形")

    def test_parse_convert_subcommand(self):
        ns = self.p.parse_args(["convert", "report.pdf", "--out", "out.md"])
        self.assertEqual(ns.target, "report.pdf")
        self.assertEqual(ns.out, "out.md")

    def test_parse_tags_and_config(self):
        self.p.parse_args(["search", "tags"])
        ns = self.p.parse_args(["config", "test", "--providers", "doubao"])
        self.assertEqual(ns.providers, "doubao")


class TestCmdSearch(unittest.TestCase):
    def test_web_uses_format_search(self):
        resp = SearchResponse(query="q", results=[SearchResult(title="t", url="u")],
                              metadata={"backend": "aaa"})
        with mock.patch.object(api, "search_category", return_value=resp) as sc, \
             mock.patch("ezwork_tool.cli.format_search", return_value="ok") as fmt:
            args = argparse.Namespace(category="search.web", query="q",
                                      count=None, timeout=None, providers=None)
            cli.cmd_search(args)
            sc.assert_called_once()
            self.assertEqual(sc.call_args[0][1], "search.web")
            fmt.assert_called_once()

    def test_image_dispatches_format_image(self):
        resp = SearchResponse(query="q", metadata={"backend": "doubao"})
        with mock.patch.object(api, "search_category", return_value=resp), \
             mock.patch("ezwork_tool.cli.format_image", return_value="ok") as fmt:
            args = argparse.Namespace(category="search.image", query="q",
                                      count=None, timeout=None, providers=None,
                                      width_min=None, width_max=None,
                                      height_min=None, height_max=None, shapes=None)
            cli.cmd_search(args)
            fmt.assert_called_once()

    def test_data_dispatches_format_data(self):
        resp = SearchResponse(query="q", metadata={"backend": "anysearch"})
        with mock.patch.object(api, "search_category", return_value=resp), \
             mock.patch("ezwork_tool.cli.format_data", return_value="ok") as fmt:
            args = argparse.Namespace(category="search.data", query="q",
                                      count=None, timeout=None, providers=None,
                                      tag=None, params=None)
            cli.cmd_search(args)
            fmt.assert_called_once()


class TestCmdConvert(unittest.TestCase):
    def test_list_providers_by_category(self):
        with mock.patch.object(api, "list_category_providers") as lp, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as buf:
            lp.side_effect = lambda c: {"convert.page": ["markdown_new"],
                                        "convert.file": ["anydoc"]}[c]
            args = argparse.Namespace(list_providers=True, target=None,
                                      out=None, timeout=None, providers=None)
            cli.cmd_convert(args)
            out = buf.getvalue()
            self.assertIn("convert.page", out)
            self.assertIn("convert.file", out)
            self.assertIn("markdown_new", out)
            self.assertIn("anydoc", out)

    def test_missing_target_is_usage_error(self):
        from ezwork_tool.errors import UsageError

        args = argparse.Namespace(list_providers=False, target=None,
                                  out=None, timeout=None, providers=None)
        with self.assertRaises(UsageError):
            cli.cmd_convert(args)


if __name__ == "__main__":
    unittest.main()
