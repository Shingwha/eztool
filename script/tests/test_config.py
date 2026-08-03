"""config 公共层测试。"""

import copy
import json
import os
import tempfile
import unittest

from ezwork_tool import config as cfgmod


class TestDeepMerge(unittest.TestCase):
    def test_returns_new_dict(self):
        base = {"a": {"b": [1, 2]}}
        out = cfgmod.deep_merge(base, {})
        self.assertIsNot(out, base)
        self.assertIsNot(out["a"], base["a"])
        self.assertIsNot(out["a"]["b"], base["a"]["b"])  # list 也复制

    def test_override_without_mutating_base(self):
        base = {"providers": {"doubao": {"api_key": None, "count_web": 10}}}
        out = cfgmod.deep_merge(base, {"providers": {"doubao": {"api_key": "k"}}})
        self.assertEqual(out["providers"]["doubao"]["api_key"], "k")
        self.assertIsNone(base["providers"]["doubao"]["api_key"])  # base 未被污染
        self.assertEqual(out["providers"]["doubao"]["count_web"], 10)


class TestLoadConfig(unittest.TestCase):
    def _load_in(self, content=None):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get(cfgmod.CONFIG_DIR_ENV)
            os.environ[cfgmod.CONFIG_DIR_ENV] = d
            try:
                if content is not None:
                    os.makedirs(os.path.join(d, "x"), exist_ok=True)
                    with open(os.path.join(d, "x", "config.json"), "w", encoding="utf-8") as f:
                        f.write(content)
                    os.environ[cfgmod.CONFIG_DIR_ENV] = os.path.join(d, "x")
                return cfgmod.load_config()
            finally:
                if old is None:
                    del os.environ[cfgmod.CONFIG_DIR_ENV]
                else:
                    os.environ[cfgmod.CONFIG_DIR_ENV] = old

    def test_defaults_when_no_file(self):
        cfg = self._load_in()
        self.assertEqual(cfg["providers"]["anysearch"]["max_results"], 10)
        self.assertEqual(cfg["search"]["web"]["providers"],
                         ["doubao", "anysearch", "deepseek"])
        self.assertEqual(cfg["search"]["image"]["providers"], ["doubao"])
        self.assertEqual(cfg["search"]["paper"]["providers"],
                         ["openalex", "arxiv", "crossref"])
        self.assertEqual(cfg["search"]["data"]["providers"], ["anysearch"])
        self.assertEqual(cfg["convert"]["page"]["providers"],
                         ["markdown_new", "jina_reader", "firecrawl"])
        self.assertEqual(cfg["convert"]["file"]["providers"],
                         ["pdfinspector", "markdown_new", "mineru"])
        self.assertEqual(cfg["providers"]["deepseek"]["thinking"], "enabled")
        self.assertEqual(cfg["providers"]["mineru"]["timeout"], 300)
        self.assertEqual(cfg["providers"]["markdown_new"]["timeout"], 30)

    def test_category_section_override(self):
        cfg = self._load_in('{"search": {"web": {"providers": ["deepseek"]}}}')
        self.assertEqual(cfg["search"]["web"]["providers"], ["deepseek"])
        # 未覆盖的类别段保持默认
        self.assertEqual(cfg["search"]["image"]["providers"], ["doubao"])

    def test_corrupt_file_falls_back(self):
        cfg = self._load_in("{not json")
        self.assertEqual(cfg["providers"]["doubao"]["count_web"], 10)


class TestParseValue(unittest.TestCase):
    def test_types(self):
        self.assertEqual(cfgmod.parse_value("providers.doubao.timeout", "45"), 45)
        self.assertIs(cfgmod.parse_value("providers.doubao.need_url", "true"), True)
        self.assertIs(cfgmod.parse_value("providers.doubao.need_url", "false"), False)
        self.assertEqual(cfgmod.parse_value("providers.doubao.industry", "finance"), "finance")
        self.assertEqual(
            cfgmod.parse_value("convert.page.providers", "jina_reader, firecrawl"),
            ["jina_reader", "firecrawl"],
        )


class TestMaskKey(unittest.TestCase):
    def test_secret_masked(self):
        self.assertEqual(
            cfgmod.mask_key("providers.doubao.api_key", "abc123456789"),
            "abc1****6789",
        )
        self.assertEqual(cfgmod.mask_key("providers.doubao.api_key", "short"), "*****")

    def test_non_secret_unmasked(self):
        self.assertEqual(cfgmod.mask_key("providers.doubao.timeout", 30), "30")


if __name__ == "__main__":
    unittest.main()
