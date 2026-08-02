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


class TestMigrateV1(unittest.TestCase):
    def test_new_structure_passthrough(self):
        raw = {"providers": {"doubao": {"api_key": "k"}}, "fetch": {"timeout": 10}}
        self.assertIsNone(cfgmod.migrate_v1(raw))

    def test_top_level_backends_moved(self):
        raw = {"doubao": {"api_key": "k"}, "anysearch": {"max_results": 5}}
        out = cfgmod.migrate_v1(raw)
        self.assertEqual(out["providers"]["doubao"]["api_key"], "k")
        self.assertEqual(out["providers"]["anysearch"]["max_results"], 5)
        self.assertNotIn("doubao", out)

    def test_fetch_convert_subsections_merged(self):
        raw = {
            "fetch": {"providers": ["firecrawl"], "timeout": 30,
                      "firecrawl": {"api_key": "f"}, "jina": {"timeout": 5}},
            "convert": {"providers": ["mineru"], "timeout": 60,
                        "mineru": {"api_key": "m", "timeout": 120}},
        }
        out = cfgmod.migrate_v1(raw)
        self.assertEqual(out["providers"]["firecrawl"]["api_key"], "f")
        self.assertEqual(out["providers"]["mineru"], {"api_key": "m", "timeout": 120})
        self.assertEqual(out["fetch"], {"providers": ["firecrawl"], "timeout": 30})
        self.assertEqual(out["convert"], {"providers": ["mineru"], "timeout": 60})

    def test_mineru_key_deduplicated(self):
        raw = {
            "fetch": {"mineru": {"api_key": "k1"}},
            "convert": {"mineru": {"api_key": "k2", "timeout": 9}},
        }
        out = cfgmod.migrate_v1(raw)
        self.assertEqual(out["providers"]["mineru"]["api_key"], "k2")  # convert 覆盖 fetch
        self.assertEqual(out["providers"]["mineru"]["timeout"], 9)


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
        self.assertEqual(cfg["fetch"]["providers"], ["firecrawl", "markdown", "jina"])
        self.assertEqual(cfg["providers"]["deepseek"]["thinking"], "enabled")
        self.assertEqual(cfg["providers"]["mineru"]["timeout"], 300)

    def test_corrupt_file_falls_back(self):
        cfg = self._load_in("{not json")
        self.assertEqual(cfg["providers"]["doubao"]["count_web"], 10)

    def test_v1_file_auto_migrated_and_saved(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get(cfgmod.CONFIG_DIR_ENV)
            os.environ[cfgmod.CONFIG_DIR_ENV] = d
            try:
                path = os.path.join(d, "config.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"doubao": {"api_key": "k"}, "fetch": {"mineru": {"api_key": "m"}}}, f)
                cfg = cfgmod.load_config()
                self.assertEqual(cfg["providers"]["doubao"]["api_key"], "k")
                self.assertEqual(cfg["providers"]["mineru"]["api_key"], "m")
                # 迁移已写回：文件里不再有旧顶层段
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertNotIn("doubao", saved)
                self.assertEqual(saved["providers"]["doubao"]["api_key"], "k")
            finally:
                if old is None:
                    del os.environ[cfgmod.CONFIG_DIR_ENV]
                else:
                    os.environ[cfgmod.CONFIG_DIR_ENV] = old


class TestParseValue(unittest.TestCase):
    def test_types(self):
        self.assertEqual(cfgmod.parse_value("providers.doubao.timeout", "45"), 45)
        self.assertIs(cfgmod.parse_value("providers.doubao.need_url", "true"), True)
        self.assertIs(cfgmod.parse_value("providers.doubao.need_url", "false"), False)
        self.assertEqual(cfgmod.parse_value("providers.doubao.industry", "finance"), "finance")
        self.assertEqual(
            cfgmod.parse_value("fetch.providers", "jina, firecrawl"),
            ["jina", "firecrawl"],
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
