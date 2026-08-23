"""config 模块：三段式 DEFAULTS / parse_value / mask_key / save-load roundtrip / 损坏回退。"""

import json

from eztool import config as cfgmod
from eztool import provider as prov


class TestDefaults:
    def test_three_section_structure(self):
        assert set(cfgmod.DEFAULTS) == {"settings", "chains", "providers"}
        assert cfgmod.DEFAULTS["settings"]["timeout"] == 30
        assert set(cfgmod.DEFAULTS["chains"]) == {"web", "image", "data", "page", "file"}
        assert set(cfgmod.DEFAULTS["providers"]) == set(prov.SERVICES)

    def test_chains_default_equals_default_chain(self):
        for cat in ("web", "image", "data", "page", "file"):
            assert cfgmod.DEFAULTS["chains"][cat] == prov.default_chain(cat)

    def test_provider_sections_have_timeout_and_declared_keys(self):
        for name, keys in prov.provider_config_map().items():
            sec = cfgmod.DEFAULTS["providers"][name]
            assert "timeout" in sec
            for key, meta in keys.items():
                assert sec[key] == meta["default"]

    def test_secret_keys_cover_declared_secrets(self):
        assert "providers.doubao.api_key" in cfgmod.SECRET_KEYS
        assert "providers.doubao.sk" in cfgmod.SECRET_KEYS
        assert "providers.doubao.count_web" not in cfgmod.SECRET_KEYS


class TestParseValue:
    def test_chains_split_comma(self):
        assert cfgmod.parse_value("chains.web", " a , b ,,c ") == ["a", "b", "c"]

    def test_scalar_types(self):
        assert cfgmod.parse_value("providers.x.flag", "true") is True
        assert cfgmod.parse_value("providers.x.flag", "FALSE") is False
        assert cfgmod.parse_value("settings.timeout", "60") == 60
        assert cfgmod.parse_value("providers.x.api_key", "sk-123abc") == "sk-123abc"


class TestMaskKey:
    def test_masking_rules(self):
        # 长 secret 首尾各留 4 位；短 secret 全星号；非 secret / 空值原样
        assert cfgmod.mask_key("providers.doubao.api_key", "abcd1234wxyz") == "abcd****wxyz"
        assert cfgmod.mask_key("providers.doubao.api_key", "short") == "*****"
        assert cfgmod.mask_key("providers.doubao.count_web", 20) == "20"
        assert cfgmod.mask_key("providers.doubao.api_key", "") == ""
        assert cfgmod.mask_key("providers.doubao.api_key", None) == ""


class TestLoadSave:
    def test_roundtrip(self, isolated_config):
        cfg = cfgmod.load_config()  # 无文件 → 默认
        cfgmod.set_key(cfg, "providers.doubao.api_key", "k1")
        cfgmod.set_key(cfg, "settings.timeout", 42)
        cfgmod.save_config(cfg)
        loaded = cfgmod.load_config()
        assert cfgmod.get_key(loaded, "providers.doubao.api_key") == "k1"
        assert cfgmod.get_key(loaded, "settings.timeout") == 42
        # 未改的键仍是默认（深合并）
        assert cfgmod.get_key(loaded, "chains.web") == prov.default_chain("web")

    def test_corrupted_file_falls_back_to_defaults(self, isolated_config):
        path = isolated_config / "config.json"
        path.write_text("{ not json !!!", encoding="utf-8")
        assert cfgmod.load_config() == cfgmod.DEFAULTS
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")  # 非 dict 同样回退
        assert cfgmod.load_config() == cfgmod.DEFAULTS

    def test_sparse_file_deep_merges(self, isolated_config):
        path = isolated_config / "config.json"
        path.write_text(json.dumps({"settings": {"timeout": 7}}), encoding="utf-8")
        cfg = cfgmod.load_config()
        assert cfg["settings"]["timeout"] == 7
        assert cfg["chains"] == cfgmod.DEFAULTS["chains"]  # 未覆盖段保留默认
