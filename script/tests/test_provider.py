"""provider 注册表与元数据测试：注册、冲突、默认链派生、配置键聚合。"""

import unittest

from ezwork_tool import provider as prov
from ezwork_tool.provider import ParamSpec, Provider, register


def _make(name, **attrs):
    cls = type(f"Fake{name.title()}", (Provider,), {"name": name})
    for k, v in attrs.items():
        setattr(cls, k, v)
    return cls


class TestRegister(unittest.TestCase):
    def tearDown(self):
        # 清掉测试注入的假 provider，避免污染其他测试
        for n in list(prov.SERVICES):
            if n.startswith("fake_"):
                del prov.SERVICES[n]

    def test_register_and_lookup(self):
        cls = _make("fake_a", categories=frozenset({"search.web"}))
        register(cls)
        self.assertIs(prov.SERVICES["fake_a"], cls)

    def test_duplicate_name_rejected(self):
        register(_make("fake_b", categories=frozenset({"search.web"})))
        with self.assertRaises(ValueError):
            register(_make("fake_b"))

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            register(type("NoName", (Provider,), {"name": ""}))


class TestCategoryParams(unittest.TestCase):
    """同名参数跨 provider 冲突 → 注册期报错（防静默覆盖）。"""

    def tearDown(self):
        for n in list(prov.SERVICES):
            if n.startswith("fake_"):
                del prov.SERVICES[n]

    def test_union_and_collision(self):
        register(_make("fake_c1", categories=frozenset({"search.web"}),
                        params={"search.web": {"alpha": ParamSpec(type=int)}}))
        register(_make("fake_c2", categories=frozenset({"search.web"}),
                        params={"search.web": {"beta": ParamSpec()}}))
        self.assertEqual(set(prov.category_params("search.web")),
                         {"alpha", "beta"})
        register(_make("fake_c3", categories=frozenset({"search.web"}),
                        params={"search.web": {"alpha": ParamSpec()}}))
        with self.assertRaises(ValueError):
            prov.category_params("search.web")


class TestDefaultChain(unittest.TestCase):
    """默认链 = priority 排序；未声明 priority 不进链。"""

    def tearDown(self):
        for n in list(prov.SERVICES):
            if n.startswith("fake_"):
                del prov.SERVICES[n]

    def test_sorted_and_excluded(self):
        register(_make("fake_low", categories=frozenset({"search.web"}),
                        priority={"search.web": 30}))
        register(_make("fake_high", categories=frozenset({"search.web"}),
                        priority={"search.web": 10}))
        register(_make("fake_out", categories=frozenset({"search.web"})))  # 无 priority
        chain = prov.default_chain("search.web")
        fakes = [n for n in chain if n.startswith("fake_")]
        self.assertEqual(fakes, ["fake_high", "fake_low"])  # 相对顺序正确
        self.assertNotIn("fake_out", chain)

    def test_unknown_category_empty(self):
        self.assertEqual(prov.default_chain("search.xxx"), [])


class TestProviderConfigMap(unittest.TestCase):
    """config 键声明聚合：默认值/secret/hint 齐全。"""

    def test_all_providers_declared(self):
        m = prov.provider_config_map()
        self.assertEqual(set(m), set(prov.SERVICES))
        for name, keys in m.items():
            for key, meta in keys.items():
                self.assertIn("default", meta)
                self.assertIn("secret", meta)
                self.assertIn("hint", meta)

    def test_doubao_meta(self):
        m = prov.provider_config_map()
        self.assertTrue(m["doubao"]["api_key"]["secret"])
        self.assertEqual(m["doubao"]["count_web"]["default"], 20)
        self.assertTrue(m["deepseek"]["api_key"]["secret"])
        self.assertTrue(m["exa"]["api_key"]["secret"])  # auth_required 配套


class TestAuthRequired(unittest.TestCase):
    """必须配凭证的 provider 名单：doubao / deepseek / exa。"""

    def test_auth_required_set(self):
        required = {n for n, cls in prov.SERVICES.items() if cls.auth_required}
        self.assertEqual(required, {"doubao", "deepseek", "exa"})

    def test_anonymous_providers_not_required(self):
        for n in ("anysearch", "tavily", "firecrawl", "jina_reader",
                  "markdown_new", "mineru", "anydoc"):
            self.assertFalse(prov.SERVICES[n].auth_required, n)


if __name__ == "__main__":
    unittest.main()
