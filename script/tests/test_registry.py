"""注册表与元数据聚合：register / providers_for / default_chain / category_params /
provider_config_map / all_sources。"""

import pytest

from eztool import provider as prov
from eztool.provider import ParamSpec, Provider, register


def _make(name, **attrs):
    return type(f"P_{name}", (Provider,), {"name": name, **attrs})


class TestRegister:
    def test_register_and_lookup(self):
        cls = register(_make("fake_reg", categories=frozenset({"web"})))
        assert prov.SERVICES["fake_reg"] is cls

    def test_duplicate_name_rejected(self):
        register(_make("fake_dup", categories=frozenset({"web"})))
        with pytest.raises(ValueError, match="duplicate"):
            register(_make("fake_dup"))

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            register(_make(""))


class TestProvidersFor:
    def test_filters_by_category_and_unknown_is_empty(self):
        register(_make("fake_web", categories=frozenset({"web"})))
        register(_make("fake_file", categories=frozenset({"file"})))
        assert "fake_web" in prov.providers_for("web")
        assert "fake_web" not in prov.providers_for("file")
        assert prov.providers_for("nope") == []


class TestDefaultChain:
    def test_priority_sorted_and_undeclared_excluded(self):
        register(_make("fake_low", categories=frozenset({"web"}),
                       priority={"web": 30}))
        register(_make("fake_high", categories=frozenset({"web"}),
                       priority={"web": 10}))
        register(_make("fake_out", categories=frozenset({"web"})))  # 无 priority
        chain = prov.default_chain("web")
        fakes = [n for n in chain if n.startswith("fake_")]
        assert fakes == ["fake_high", "fake_low"]
        assert "fake_out" not in chain

    def test_real_chains_cover_five_categories(self):
        # 出厂默认链由 priority 声明派生；五个类别都应有定义（可为空链）
        import eztool.providers  # noqa: F401 确保真实 provider 已注册
        for cat in ("web", "image", "data", "page", "file"):
            assert isinstance(prov.default_chain(cat), list)
        assert prov.default_chain("web")[0] == "doubao"  # priority=10 最前


class TestCategoryParams:
    def test_category_params_union_and_collision(self):
        register(_make("fake_p1", categories=frozenset({"web"}),
                       params={"web": {"alpha": ParamSpec(type=int)}}))
        register(_make("fake_p2", categories=frozenset({"web"}),
                       params={"web": {"beta": ParamSpec()}}))
        assert {"alpha", "beta"} <= set(prov.category_params("web"))  # 并集
        register(_make("fake_c1", categories=frozenset({"web"}),
                       params={"web": {"clash": ParamSpec()}}))
        register(_make("fake_c2", categories=frozenset({"web"}),
                       params={"web": {"clash": ParamSpec()}}))
        with pytest.raises(ValueError, match="clash"):
            prov.category_params("web")


class TestMetadataAggregation:
    def test_provider_config_map_complete(self):
        m = prov.provider_config_map()
        assert set(m) == set(prov.SERVICES)
        for name, keys in m.items():
            for key, meta in keys.items():
                assert {"default", "secret", "hint"} <= set(meta), (name, key)

    def test_secret_flags_and_defaults(self):
        m = prov.provider_config_map()
        assert m["doubao"]["api_key"]["secret"] is True
        assert m["doubao"]["count_web"]["default"] == 20
        assert m["exa"]["api_key"]["secret"] is True  # auth_required 配套

    def test_auth_required_set(self):
        required = {n for n, cls in prov.SERVICES.items() if cls.auth_required}
        assert required == {"doubao", "deepseek", "exa", "parallel"}

    def test_all_sources_aggregates_40_tags(self):
        sources = prov.all_sources()
        assert len(sources) == 40
        tags = [t for t, _ in sources]
        assert "finance.quote" in tags and "general.general" in tags
