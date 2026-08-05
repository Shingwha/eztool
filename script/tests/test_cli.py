"""CLI 命令面测试：子命令结构、参数面、--list-providers、config 子命令。"""

import io
import unittest
from contextlib import redirect_stdout, redirect_stderr

from ezwork_tool import cli


class TestParserStructure(unittest.TestCase):
    def test_commands(self):
        p = cli.build_parser()
        subs = {a.dest: a for a in p._actions if a.dest == "command"}
        parser = subs["command"]
        self.assertEqual(set(parser.choices), {"search", "convert", "config"})

    def test_search_subcommands(self):
        p = cli.build_parser()
        search = next(a for a in p._actions
                      if getattr(a, "dest", None) == "command").choices["search"]
        ssub = next(a for a in search._actions if a.dest == "search_cmd")
        self.assertEqual(set(ssub.choices), {"web", "image", "data", "tags"})

    def test_search_web_params(self):
        p = cli.build_parser()
        web = self._search_sub("web")
        opts = [a.dest for a in web._actions]
        for want in ("query", "count", "timeout", "providers", "list_providers"):
            self.assertIn(want, opts)

    def test_search_image_has_doubao_params(self):
        img = self._search_sub("image")
        opts = {a.dest: a for a in img._actions}
        for want in ("width_min", "width_max", "shapes"):
            self.assertIn(want, opts)

    def test_search_data_has_anysearch_params(self):
        data = self._search_sub("data")
        opts = {a.dest: a for a in data._actions}
        self.assertIn("tag", opts)
        self.assertIn("params", opts)

    def test_config_subcommands(self):
        p = cli.build_parser()
        config = next(a for a in p._actions
                      if getattr(a, "dest", None) == "command").choices["config"]
        csub = next(a for a in config._actions if a.dest == "config_cmd")
        self.assertEqual(set(csub.choices),
                         {"show", "set", "get", "reset", "test", "clear"})

    def _search_sub(self, name):
        p = cli.build_parser()
        search = next(a for a in p._actions
                      if getattr(a, "dest", None) == "command").choices["search"]
        ssub = next(a for a in search._actions if a.dest == "search_cmd")
        return ssub.choices[name]


class TestListProviders(unittest.TestCase):
    def test_convert_list(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["convert", "--list-providers"])
        text = out.getvalue()
        self.assertIn("convert.page", text)
        self.assertIn("convert.file", text)
        self.assertIn("markdown_new", text)
        self.assertIn("anydoc", text)

    def test_search_list(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["search", "web", "q", "--list-providers"])
        text = out.getvalue()
        self.assertIn("search.web", text)
        self.assertIn("doubao", text)


class TestExitCodes(unittest.TestCase):
    def test_usage_error_exit_2(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            cli.main(["convert"])  # 缺 target
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_config_key(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            cli.main(["config", "get", "no.such.key"])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_provider_param_not_crash(self):
        # 未知 provider 名（显式）→ UsageError → exit 2
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            cli.main(["search", "web", "q", "--providers", "ghost"])
        self.assertEqual(ctx.exception.code, 2)


class TestConfigCommands(unittest.TestCase):
    def test_show_lists_generated_keys(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["config", "show"])
        text = out.getvalue()
        self.assertIn("config file:", text)
        self.assertIn("providers.doubao.api_key", text)
        self.assertIn("search.web.providers", text)

    def test_show_masks_secrets(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["config", "show"])
        text = out.getvalue()
        for line in text.splitlines():
            if line.startswith("providers.doubao.api_key ="):
                self.assertNotIn("K83ZyzR2q1BsI6SRC7z4dw5rXfrc6kCh", line)


if __name__ == "__main__":
    unittest.main()
