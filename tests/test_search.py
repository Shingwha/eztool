"""搜索路由与参数校验测试。"""

import unittest

from ezwork_tool.errors import UsageError
from ezwork_tool.search import check_params, resolve_backend


class TestResolveBackend(unittest.TestCase):
    def test_explicit_backend_passthrough(self):
        self.assertEqual(resolve_backend("doubao", {}), "doubao")
        self.assertEqual(resolve_backend("deepseek", {}), "deepseek")

    def test_auto_no_credentials_falls_back_to_anysearch(self):
        cfg = {"doubao": {"api_key": None, "ak": None, "sk": None},
               "deepseek": {"api_key": None}}
        self.assertEqual(resolve_backend("auto", cfg), "anysearch")

    def test_auto_doubao_api_key_wins(self):
        cfg = {"doubao": {"api_key": "k", "ak": None, "sk": None},
               "deepseek": {"api_key": None}}
        self.assertEqual(resolve_backend("auto", cfg), "doubao")

    def test_auto_doubao_aksk_wins(self):
        cfg = {"doubao": {"api_key": None, "ak": "a", "sk": "s"},
               "deepseek": {"api_key": None}}
        self.assertEqual(resolve_backend("auto", cfg), "doubao")

    def test_auto_deepseek_second(self):
        cfg = {"doubao": {"api_key": None, "ak": None, "sk": None},
               "deepseek": {"api_key": "k"}}
        self.assertEqual(resolve_backend("auto", cfg), "deepseek")


class TestCheckParams(unittest.TestCase):
    def test_anysearch_param_on_doubao_rejected(self):
        with self.assertRaises(UsageError):
            check_params("doubao", {"tag": "general.general"})

    def test_doubao_param_on_anysearch_rejected(self):
        with self.assertRaises(UsageError):
            check_params("anysearch", {"image": True})

    def test_own_params_ok(self):
        check_params("doubao", {"image": True, "sites": "a.com"})  # 不抛
        check_params("anysearch", {"tag": "x", "zone": "cn"})
        check_params("deepseek", {"count": 5})  # 公共参数不校验

    def test_falsy_params_ignored(self):
        check_params("doubao", {"tag": None, "anonymous": False})  # 不抛


if __name__ == "__main__":
    unittest.main()
