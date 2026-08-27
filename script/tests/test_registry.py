"""注册表与元数据聚合：register / providers_for / default_chain /
provider_config_map。"""

import pytest

from eztool import provider as prov
from eztool.provider import Provider, register


def _make(name, **attrs):
    return type(f"P_{name}", (Provider,), {"name": name, **attrs})


class TestRegister:
    def test_register_contract(self):
        register(_make("fake_reg", categories=frozenset({"web"})))
        assert prov.SERVICES["fake_reg"].name == "fake_reg"
        with pytest.raises(ValueError, match="duplicate"):
            register(_make("fake_reg"))
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

    def test_real_chains_derived_from_priorities(self):
        # 出厂默认链由 priority 声明派生；三个类别都应有定义（文档承诺的顺序）
        import eztool.providers  # noqa: F401 确保真实 provider 已注册
        assert prov.default_chain("web") == [
            "tavily", "doubao", "anysearch", "keen", "parallel",
        ]
        assert prov.default_chain("page") == [
            "markdown_new", "tavily", "jina_reader", "firecrawl", "keen", "parallel",
        ]
        assert prov.default_chain("file") == ["anydoc", "markdown_new", "mineru"]


class TestMetadataAggregation:
    def test_provider_config_map_complete(self):
        m = prov.provider_config_map()
        assert set(m) == set(prov.SERVICES)
        for name, keys in m.items():
            for key, meta in keys.items():
                assert {"default", "secret", "hint"} <= set(meta), (name, key)

    def test_auth_required_set(self):
        required = {n for n, cls in prov.SERVICES.items() if cls.auth_required}
        assert required == {"doubao", "parallel"}
