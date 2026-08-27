"""HTTP 辅助函数的默认 User-Agent 注入。

全部 mock urlopen，捕获实际发出的 Request 断言 headers。
"""

import urllib.request
from unittest import mock

from eztool.util import USER_AGENT, http_get, post_json

from conftest import make_response


def _capture(monkeypatch):
    """替换 urlopen，返回可读到的已发请求列表。"""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return make_response(200, b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_default_user_agent_injected(monkeypatch):
    seen = _capture(monkeypatch)
    http_get("https://svc.test/x", {"Accept": "text/markdown"}, 5)
    # urllib 默认 Python-urllib UA 会被 Cloudflare 类网关 403，必须补常量
    assert seen[0].get_header("User-agent") == USER_AGENT


def test_explicit_user_agent_preserved(monkeypatch):
    seen = _capture(monkeypatch)
    http_get("https://svc.test/x", {"User-Agent": "custom-agent/1.0"}, 5)
    assert seen[0].get_header("User-agent") == "custom-agent/1.0"


def test_post_json_headers(monkeypatch):
    seen = _capture(monkeypatch)
    post_json("https://svc.test/x", {"Authorization": "Bearer k"}, {"q": 1}, 5)
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert seen[0].get_header("Content-type") == "application/json"
