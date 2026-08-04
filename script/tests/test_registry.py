"""registry 类别注册表测试：类别注册、参数归属、providers_for、重复校验。"""

import unittest

from ezwork_tool.base import ParamSpec, Provider, ProviderOpts
from ezwork_tool.errors import CATEGORY_INVALID, ServiceError
from ezwork_tool.registry import (
    CATEGORIES,
    SERVICES,
    category_params,
    convert_file_services,
    convert_page_services,
    providers_for,
    register,
    search_categories,
    service_names,
)


class _RegProvider(Provider):
    name = ""
    categories = frozenset()
    category_params = {}

    def search(self, cfg, query, opts):
        from ezwork_tool.base import SearchResponse
        return SearchResponse(query=query)


def _make(name, categories, params=None):
    return type(f"Reg{name.title()}", (_RegProvider,), {
        "name": name,
        "categories": frozenset(categories),
        "category_params": params or {},
    })


class TestRegister(unittest.TestCase):
    def setUp(self):
        self._orig = {c: list(v) for c, v in CATEGORIES.items()}
        CATEGORIES.clear()

    def tearDown(self):
        for name in list(SERVICES):
            if name.startswith("reg_"):
                SERVICES.pop(name, None)
        CATEGORIES.clear()
        CATEGORIES.update(self._orig)

    def test_registers_into_categories_in_order(self):
        @register
        class RegA(_RegProvider):
            name = "reg_a"
            categories = frozenset({"search.web", "search.image"})

        @register
        class RegB(_RegProvider):
            name = "reg_b"
            categories = frozenset({"search.web"})

        self.assertEqual(providers_for("search.web"), ["reg_a", "reg_b"])
        self.assertEqual(providers_for("search.image"), ["reg_a"])

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            register(_make("", {"search.web"}))

    def test_duplicate_name_rejected(self):
        register(_make("reg_dup", {"search.web"}))
        with self.assertRaises(ValueError):
            register(_make("reg_dup", {"search.image"}))

    def test_invalid_category_rejected(self):
        with self.assertRaises(ValueError):
            register(_make("reg_bad", {"search"}))          # 无 <域>.<操作>
        with self.assertRaises(ValueError):
            register(_make("reg_bad2", {"Search.web"}))     # 大写
        with self.assertRaises(ValueError):
            register(_make("reg_bad3", {"search.9web"}))    # 数字开头

    def test_duplicate_param_in_same_category_rejected(self):
        register(_make("reg_p1", {"search.web"},
                       {"search.web": {"tag": ParamSpec()}}))
        with self.assertRaises(ValueError):
            register(_make("reg_p2", {"search.web"},
                           {"search.web": {"tag": ParamSpec()}}))

    def test_public_param_collision_rejected(self):
        """provider 不得声明与类别公共参数同名的参数。"""
        with self.assertRaises(ValueError):
            register(_make("reg_pp", {"search.web"},
                           {"search.web": {"include_domains": ParamSpec()}}))

    def test_reserved_param_name_rejected(self):
        """provider 不得声明 CLI 内部字段名（会篡改路由/分派）。"""
        for reserved in ("category", "func", "command", "query", "count"):
            with self.assertRaises(ValueError, msg=reserved):
                register(_make(f"reg_r_{reserved}", {"search.web"},
                               {"search.web": {reserved: ParamSpec()}}))

    def test_same_param_in_different_category_ok(self):
        register(_make("reg_q1", {"search.web", "search.data"},
                       {"search.web": {"tag": ParamSpec()},
                        "search.data": {"tag": ParamSpec()}}))  # 不抛


class TestLookups(unittest.TestCase):
    def setUp(self):
        self._orig = {c: list(v) for c, v in CATEGORIES.items()}
        CATEGORIES.clear()
        SERVICES["look_a"] = _make("look_a", {"search.web", "search.paper"},
                                   {"search.web": {"foo": ParamSpec()}})
        SERVICES["look_b"] = _make("look_b", {"search.web"},
                                   {"search.web": {"bar": ParamSpec(type=int)}})
        CATEGORIES["search.web"] = ["look_a", "look_b"]
        CATEGORIES["search.paper"] = ["look_a"]

    def tearDown(self):
        CATEGORIES.clear()
        CATEGORIES.update(self._orig)
        SERVICES.pop("look_a", None)
        SERVICES.pop("look_b", None)
        SERVICES.pop("look_c", None)

    def test_providers_for_unknown_category(self):
        with self.assertRaises(ServiceError) as ctx:
            providers_for("search.xyz")
        self.assertEqual(ctx.exception.category, CATEGORY_INVALID)

    def test_category_params_union(self):
        params = category_params("search.web")
        self.assertLessEqual({"foo", "bar"}, set(params))
        self.assertEqual(params["bar"].type, int)

    def test_category_params_include_public(self):
        """类别公共参数（PUBLIC_PARAMS）自动并入，无归属 provider。"""
        params = category_params("search.web")
        self.assertIn("include_domains", params)

    def test_category_params_order_is_registration_order(self):
        """provider 参数按注册顺序在前，公共参数追加在后。"""
        params = list(category_params("search.web"))
        self.assertEqual(params[:2], ["foo", "bar"])
        self.assertEqual(params[2:], ["include_domains"])

    def test_search_categories_sorted(self):
        self.assertEqual(search_categories(), ["search.paper", "search.web"])

    def test_convert_service_lists(self):
        SERVICES["look_c"] = _make("look_c", {"convert.page", "convert.file"})
        CATEGORIES["convert.page"] = ["look_c"]
        CATEGORIES["convert.file"] = ["look_c"]
        self.assertEqual(convert_page_services(), ["look_c"])
        self.assertEqual(convert_file_services(), ["look_c"])

    def test_service_names_sorted(self):
        names = service_names()
        self.assertEqual(names, sorted(names))
        self.assertIn("look_a", names)

    def test_create_service_unknown(self):
        with self.assertRaises(ServiceError):
            from ezwork_tool.registry import create_service
            create_service("nope")
        svc = create_service("look_a", ProviderOpts())
        self.assertEqual(svc.name, "look_a")


if __name__ == "__main__":
    unittest.main()
