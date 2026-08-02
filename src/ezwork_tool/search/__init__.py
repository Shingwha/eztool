"""搜索路由：后端选择（auto）+ 参数归属校验。"""

from __future__ import annotations

from .. import config as cfgmod
from ..errors import UsageError
from .base import SearchResponse

BACKEND_NAMES = ("auto", "doubao", "anysearch", "deepseek")

# 特有参数 → 归属后端（不在此表的视为公共参数：count/timeout/full/backend）
PARAM_OWNER = {
    "tag": "anysearch", "zone": "anysearch", "language": "anysearch",
    "params": "anysearch", "anonymous": "anysearch",
    "image": "doubao", "sites": "doubao", "block_hosts": "doubao",
    "time_range": "doubao", "need_content": "doubao", "need_url": "doubao",
    "content_formats": "doubao", "industry": "doubao",
    "query_rewrite": "doubao", "auth_info_level": "doubao",
    "width_min": "doubao", "width_max": "doubao",
    "height_min": "doubao", "height_max": "doubao", "shapes": "doubao",
}


def resolve_backend(backend: str, cfg: dict) -> str:
    """auto：已配置凭证优先 doubao → deepseek，否则 anysearch（匿名兜底）。"""
    if backend != "auto":
        return backend
    if cfgmod.get_key(cfg, "doubao.api_key") or (
        cfgmod.get_key(cfg, "doubao.ak") and cfgmod.get_key(cfg, "doubao.sk")
    ):
        return "doubao"
    if cfgmod.get_key(cfg, "deepseek.api_key"):
        return "deepseek"
    return "anysearch"


def check_params(backend: str, opts: dict) -> None:
    """传了不属于当前后端的特有参数 → UsageError（exit 2）。"""
    for name, val in opts.items():
        if not val:
            continue
        owner = PARAM_OWNER.get(name)
        if owner and owner != backend:
            raise UsageError(
                f"参数 --{name.replace('_', '-')} 仅支持 {owner} 后端（当前: {backend}）"
            )


def run_search(cfg: dict, query: str, backend: str, opts: dict) -> SearchResponse:
    backend = resolve_backend(backend, cfg)
    check_params(backend, opts)
    if backend == "doubao":
        from . import doubao as mod
    elif backend == "anysearch":
        from . import anysearch as mod
    else:
        from . import deepseek as mod
    resp = mod.search(cfg, query, opts)
    if resp.metadata is None:
        resp.metadata = {}
    resp.metadata.setdefault("backend", backend)
    return resp
