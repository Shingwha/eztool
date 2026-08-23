"""pytest 公共 fixture：注册表快照/恢复、假 provider 工厂、配置隔离、urlopen mock 辅助。"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

import pytest

from eztool import provider as prov
from eztool.provider import (
    FetchResult,
    Provider,
    ProviderOpts,
    SearchResponse,
    SearchResult,
)
from eztool.util import ServiceError


# ── 注册表快照/恢复（autouse）：测试可安全往 SERVICES 注入假 provider ──────────


@pytest.fixture(autouse=True)
def _registry_snapshot():
    saved = dict(prov.SERVICES)
    try:
        yield
    finally:
        prov.SERVICES.clear()
        prov.SERVICES.update(saved)


# ── 假 provider 工厂（函数式，状态挂实例/闭包，不用类级可变属性）───────────────


def make_search_provider(
    name: str,
    results: list[dict] | None = None,
    fail_with: Exception | None = None,
    categories: tuple = ("web",),
    auth_required: bool = False,
    credentialed: bool = True,
    answer: str | None = None,
):
    """注册一个假搜索 provider 并返回类；``cls.calls`` 记录每次调用的现场。"""

    def search(self, category, query, opts):
        self.calls.append({
            "category": category, "query": query,
            "opts": dict(opts), "timeout": self.timeout(),
        })
        if fail_with is not None:
            raise fail_with
        return SearchResponse(
            query=query,
            results=[SearchResult(**r) for r in (results or [])],
            answer=answer,
            metadata={},
        )

    cls = type(f"Fake_{name}", (Provider,), {
        "name": name,
        "categories": frozenset(categories),
        "auth_required": auth_required,
        "has_credentials": lambda self: credentialed,
        "search": search,
        "calls": [],
    })
    prov.SERVICES[name] = cls
    return cls


def make_fetch_provider(
    name: str,
    content: str | None = "ok content",
    fail_with: Exception | None = None,
    categories: tuple = ("page",),
    auth_required: bool = False,
    credentialed: bool = True,
):
    """注册一个假 fetch/convert provider；``cls.calls`` 记录调用顺序与现场。"""

    def _invoke(self, kind, target, timeout):
        self.calls.append({"kind": kind, "target": target, "timeout": timeout})
        if fail_with is not None:
            raise fail_with
        return FetchResult(provider=self.name, content=content or "",
                           url=target, elapsed=0.0)

    cls = type(f"Fake_{name}", (Provider,), {
        "name": name,
        "categories": frozenset(categories),
        "auth_required": auth_required,
        "has_credentials": lambda self: credentialed,
        "fetch": lambda self, url, timeout=30: _invoke(self, "fetch", url, timeout),
        "convert_file": lambda self, path, timeout=60: _invoke(
            self, "convert_file", path, timeout),
        "calls": [],
    })
    prov.SERVICES[name] = cls
    return cls


# ── 配置隔离：EZTOOL_CONFIG_DIR 指向 tmp_path；cfg dict 工厂 ──────────────────


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """把配置目录隔离到 tmp_path（config show/set/get 等读写不进真实 HOME）。"""
    monkeypatch.setenv("EZTOOL_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def base_cfg():
    """构造最小可用 cfg dict（api 层直接吃 dict，不经过 load_config）。"""

    def _make(chains=None, providers=None, settings_timeout=30):
        return {
            "settings": {"timeout": settings_timeout},
            "chains": chains or {},
            "providers": providers or {},
        }

    return _make


@pytest.fixture
def popts():
    """按给定 configs/timeouts 构造 ProviderOpts 的快捷方式。"""

    def _make(configs=None, timeouts=None):
        return ProviderOpts(timeouts=timeouts or {}, configs=configs or {})

    return _make


# ── urlopen mock 辅助（全部测试禁止真实网络）─────────────────────────────────


def make_response(status: int = 200, body: bytes | str = b"", headers=None):
    """urllib 响应 mock：支持 ``with`` 与 (status, headers, read()) 解构。"""
    resp = mock.MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.read.return_value = body.encode("utf-8") if isinstance(body, str) else body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def json_response(payload: dict, status: int = 200):
    return make_response(status, json.dumps(payload).encode("utf-8"))


def make_http_error(code: int, payload: dict | None = None):
    """HTTPError 工厂：错误体默认带 {"code", "msg"} JSON。"""
    body = json.dumps(payload or {"code": code, "msg": "err"}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://svc.test/x", code, "err", {}, io.BytesIO(body)
    )


__all__ = [
    "ServiceError",
    "make_search_provider",
    "make_fetch_provider",
    "make_response",
    "json_response",
    "make_http_error",
]
