"""config 公共层测试：元数据生成默认值、读写、解析、脱敏。"""

import json
import os
import tempfile
import unittest

from ezwork_tool import config as cfgmod
from ezwork_tool import provider as prov


class TestGeneratedDefaults(unittest.TestCase):
    def test_provider_sections_from_meta(self):
        meta = prov.provider_config_map()
        for name, cls in prov.SERVICES.items():
            sec = cfgmod.DEFAULTS["providers"][name]
            for key, entry in meta[name].items():
                self.assertIn(key, sec, f"{name}.{key}")
                self.assertEqual(sec[key], entry["default"])

    def test_chain_defaults_match_priority(self):
        self.assertEqual(cfgmod.DEFAULTS["search"]["web"]["providers"],
                         prov.default_chain("search.web"))
        self.assertEqual(cfgmod.DEFAULTS["convert"]["page"]["providers"],
                         prov.default_chain("convert.page"))
        self.assertEqual(cfgmod.DEFAULTS["convert"]["file"]["providers"],
                         prov.default_chain("convert.file"))

    def test_exa_not_in_default_chains(self):
        for chain in ("search.web", "convert.page"):
            self.assertNotIn("exa", cfgmod.DEFAULTS[chain.split(".")[0]][chain.split(".")[1]]["providers"])

    def test_special_timeouts_from_meta(self):
        self.assertEqual(cfgmod.DEFAULTS["providers"]["jina_reader"]["timeout"], 10)
        self.assertEqual(cfgmod.DEFAULTS["providers"]["mineru"]["timeout"], 300)
        self.assertEqual(cfgmod.DEFAULTS["providers"]["firecrawl"]["timeout"], 60)

    def test_secret_keys_generated(self):
        self.assertIn("providers.doubao.api_key", cfgmod.SECRET_KEYS)
        self.assertIn("providers.exa.api_key", cfgmod.SECRET_KEYS)
        self.assertNotIn("providers.doubao.count_web", cfgmod.SECRET_KEYS)
        self.assertNotIn("providers.markdown_new.timeout", cfgmod.SECRET_KEYS)

    def test_key_hints_generated(self):
        self.assertIn("providers.doubao.api_key", cfgmod.KEY_HINTS)
        self.assertIn("search.web.providers", cfgmod.KEY_HINTS)
        self.assertIn("convert.file.timeout", cfgmod.KEY_HINTS)


class TestConfigIO(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old = os.environ.get("EZTOOL_CONFIG_DIR")
        os.environ["EZTOOL_CONFIG_DIR"] = self._tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("EZTOOL_CONFIG_DIR", None)
        else:
            os.environ["EZTOOL_CONFIG_DIR"] = self._old

    def test_save_load_roundtrip(self):
        cfg = cfgmod.load_config()
        cfgmod.set_key(cfg, "providers.doubao.timeout", 99)
        cfgmod.save_config(cfg)
        cfg2 = cfgmod.load_config()
        self.assertEqual(cfgmod.get_key(cfg2, "providers.doubao.timeout"), 99)

    def test_corrupt_config_falls_back(self):
        path = cfgmod.config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        cfg = cfgmod.load_config()
        self.assertIn("api_key", cfg["providers"]["doubao"])  # 回退默认结构

    def test_deep_merge_copies(self):
        base = {"a": {"b": [1, 2]}}
        merged = cfgmod.deep_merge(base, {"a": {"c": 3}})
        self.assertEqual(merged["a"], {"b": [1, 2], "c": 3})
        self.assertEqual(base["a"], {"b": [1, 2]})  # 原对象不变
        merged["a"]["b"].append(9)
        self.assertEqual(base["a"]["b"], [1, 2])  # 深拷贝


class TestParseAndMask(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(cfgmod.parse_value("search.web.providers", "a, b"),
                         ["a", "b"])
        self.assertIs(cfgmod.parse_value("x", "true"), True)
        self.assertIs(cfgmod.parse_value("x", "false"), False)
        self.assertEqual(cfgmod.parse_value("x", "42"), 42)
        self.assertEqual(cfgmod.parse_value("x", "hello"), "hello")

    def test_mask_key(self):
        self.assertEqual(cfgmod.mask_key("providers.doubao.api_key", "short"),
                         "*****")
        masked = cfgmod.mask_key("providers.doubao.api_key", "1234567890abcdef")
        self.assertEqual(masked, "1234****cdef")
        self.assertEqual(cfgmod.mask_key("providers.doubao.count_web", 20), "20")


if __name__ == "__main__":
    unittest.main()
