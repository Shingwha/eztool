"""config 公共层测试。"""

import copy
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
        base = {"doubao": {"api_key": None, "count_web": 10}}
        out = cfgmod.deep_merge(base, {"doubao": {"api_key": "k"}})
        self.assertEqual(out["doubao"]["api_key"], "k")
        self.assertIsNone(base["doubao"]["api_key"])  # base 未被污染
        self.assertEqual(out["doubao"]["count_web"], 10)


class TestLoadConfig(unittest.TestCase):
    def test_defaults_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get(cfgmod.CONFIG_DIR_ENV)
            os.environ[cfgmod.CONFIG_DIR_ENV] = d
            try:
                cfg = cfgmod.load_config()
            finally:
                if old is None:
                    del os.environ[cfgmod.CONFIG_DIR_ENV]
                else:
                    os.environ[cfgmod.CONFIG_DIR_ENV] = old
        self.assertEqual(cfg["anysearch"]["max_results"], 10)
        self.assertEqual(cfg["fetch"]["providers"], ["firecrawl", "markdown", "jina"])
        self.assertEqual(cfg["deepseek"]["thinking"], "enabled")

    def test_corrupt_file_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "x"), exist_ok=True)
            with open(os.path.join(d, "x", "config.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            old = os.environ.get(cfgmod.CONFIG_DIR_ENV)
            os.environ[cfgmod.CONFIG_DIR_ENV] = os.path.join(d, "x")
            try:
                cfg = cfgmod.load_config()
            finally:
                if old is None:
                    del os.environ[cfgmod.CONFIG_DIR_ENV]
                else:
                    os.environ[cfgmod.CONFIG_DIR_ENV] = old
        self.assertEqual(cfg["doubao"]["count_web"], 10)


class TestParseValue(unittest.TestCase):
    def test_types(self):
        self.assertEqual(cfgmod.parse_value("doubao.timeout", "45"), 45)
        self.assertIs(cfgmod.parse_value("doubao.need_url", "true"), True)
        self.assertIs(cfgmod.parse_value("doubao.need_url", "false"), False)
        self.assertEqual(cfgmod.parse_value("doubao.industry", "finance"), "finance")
        self.assertEqual(
            cfgmod.parse_value("fetch.providers", "jina, firecrawl"),
            ["jina", "firecrawl"],
        )


class TestMaskKey(unittest.TestCase):
    def test_secret_masked(self):
        self.assertEqual(cfgmod.mask_key("doubao.api_key", "abc123456789"), "abc1****6789")
        self.assertEqual(cfgmod.mask_key("doubao.api_key", "short"), "*****")

    def test_non_secret_unmasked(self):
        self.assertEqual(cfgmod.mask_key("doubao.timeout", 30), "30")


if __name__ == "__main__":
    unittest.main()
