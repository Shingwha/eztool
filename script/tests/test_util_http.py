"""HTTP 辅助：默认 User-Agent 注入 / 显式 UA 保留 / post_json 头。

全部 mock urlopen（UA 缺失会被 Cloudflare 类网关 403——见 d177e39 的修复）。
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


def test_user_agent_injected_or_preserved(monkeypatch):
    seen = _capture(monkeypatch)
    http_get("https://svc.test/x", {"Accept": "text/markdown"}, 5)
    assert seen[0].get_header("User-agent") == USER_AGENT
    http_get("https://svc.test/x", {"User-Agent": "custom-agent/1.0"}, 5)
    assert seen[1].get_header("User-agent") == "custom-agent/1.0"


def test_post_json_headers(monkeypatch):
    seen = _capture(monkeypatch)
    post_json("https://svc.test/x", {"Authorization": "Bearer k"}, {"q": 1}, 5)
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert seen[0].get_header("Content-type") == "application/json"
