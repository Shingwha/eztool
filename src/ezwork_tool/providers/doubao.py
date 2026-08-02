"""doubao（豆包/火山引擎 WebSearch）搜索后端。

两种鉴权（与官方文档一致）：
1. API Key (Bearer) — POST https://open.feedcoopapi.com/search_api/web_search
2. 火山引擎 AK/SK V4 签名 — POST https://mercury.volcengineapi.com?Action=WebSearch&Version=2025-01-01

纯标准库（hmac/hashlib/urllib 实现 V4 签名），无外部依赖。
从 doubao-websearch-cli 的 api.py 移植：签名与请求逻辑原样保留，CLI/配置管理不搬
（凭证统一走 ezwork_tool.config 的 doubao 段）。

对外接口（eztool 主程序依赖）：
- has_credentials(cfg) -> bool
- test_credentials(cfg) -> str
- search(cfg, query, opts) -> SearchResponse
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

from ..base import ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import BackendError, CredentialsError, NoResultsError
from ..registry import register

# --- Endpoint constants ------------------------------------------------------

# API Key endpoint (recommended)
APIKEY_HOST = "open.feedcoopapi.com"
APIKEY_PATH = "/search_api/web_search"
APIKEY_URL = f"https://{APIKEY_HOST}{APIKEY_PATH}"

# AK/SK (Volcengine TOP gateway) endpoint
AKSK_SERVICE = "volc_torchlight_api"
AKSK_VERSION = "2025-01-01"
AKSK_REGION = "cn-north-1"
AKSK_HOST = "mercury.volcengineapi.com"
AKSK_CONTENT_TYPE = "application/json"

MISSING_CRED_HINT = "未配置 doubao 凭证，请运行 eztool config set doubao.api_key"

# --- Volcengine AK/SK request signing（原样保留）----------------------------


def _norm_query(params: dict) -> str:
    """Canonical query string per Volcengine signing rules."""
    query = ""
    for key in sorted(params.keys()):
        values = params[key] if isinstance(params[key], list) else [params[key]]
        for v in values:
            query += quote(key, safe="-_.~") + "=" + quote(str(v), safe="-_.~") + "&"
    if query.endswith("&"):
        query = query[:-1]
    return query.replace("+", "%20")


def _hmac_sha256(key: bytes, content: str) -> bytes:
    return hmac.new(key, content.encode("utf-8"), hashlib.sha256).digest()


def _hash_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sign_and_send_aksk(method: str, query: dict, body: dict,
                       ak: str, sk: str, action: str, timeout: float = 30.0) -> dict:
    """Sign a request with AK/SK (HMAC-SHA256) and return the parsed JSON response.

    Implements the Volcengine V4 signature scheme used by the TOP gateway, mirroring
    the official demo but using only the standard library.

    Note: the body is serialized with the default ``json.dumps`` (ASCII-escaped,
    default separators) so that the bytes hashed for ``X-Content-Sha256`` are
    byte-identical to the bytes sent on the wire — the server verifies the
    signature against the request body, so the two must match exactly.
    """
    x_date = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    short_x_date = x_date[:8]
    body_str = json.dumps(body)
    x_content_sha256 = _hash_sha256(body_str)

    full_query = {"Action": action, "Version": AKSK_VERSION, **query}

    signed_headers_str = ";".join(["content-type", "host", "x-content-sha256", "x-date"])
    canonical_request_str = "\n".join([
        method.upper(),
        "/",
        _norm_query(full_query),
        "\n".join([
            "content-type:" + AKSK_CONTENT_TYPE,
            "host:" + AKSK_HOST,
            "x-content-sha256:" + x_content_sha256,
            "x-date:" + x_date,
        ]),
        "",
        signed_headers_str,
        x_content_sha256,
    ])

    credential_scope = "/".join([short_x_date, AKSK_REGION, AKSK_SERVICE, "request"])
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, credential_scope, _hash_sha256(canonical_request_str)
    ])

    k_date = _hmac_sha256(sk.encode("utf-8"), short_x_date)
    k_region = _hmac_sha256(k_date, AKSK_REGION)
    k_service = _hmac_sha256(k_region, AKSK_SERVICE)
    k_signing = _hmac_sha256(k_service, "request")
    signature = _hmac_sha256(k_signing, string_to_sign).hex()

    headers = {
        "Host": AKSK_HOST,
        "Content-Type": AKSK_CONTENT_TYPE,
        "X-Content-Sha256": x_content_sha256,
        "X-Date": x_date,
        "Authorization": "HMAC-SHA256 Credential={}, SignedHeaders={}, Signature={}".format(
            ak + "/" + credential_scope, signed_headers_str, signature
        ),
    }

    url = f"https://{AKSK_HOST}/?{_norm_query(full_query)}"
    return _do_request(url, headers, body_str, timeout)


# --- HTTP + response parsing（错误统一转 BackendError）-----------------------


def _do_request(url: str, headers: dict, body_str: str, timeout: float) -> dict:
    data = body_str.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return _parse_response(raw, status=e.code)
    except urllib.error.URLError as e:
        raise BackendError(
            f"网络请求失败: {e.reason}", code="network_error") from None
    return _parse_response(raw, status=status)


def _parse_response(raw: str, status: int | None = None) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise BackendError(
            f"非 JSON 响应 (HTTP {status}): {raw[:200]}", code="bad_response") from None

    meta = payload.get("ResponseMetadata") or {}
    error = meta.get("Error")
    if error or payload.get("Result") is None and status and status >= 400:
        err = error or {}
        code = str(err.get("Code", "UnknownError"))
        message = str(err.get("Message", "unknown error"))
        request_id = meta.get("RequestId")
        msg = f"[{code}] {message}"
        if request_id:
            msg += f" (request_id={request_id})"
        raise BackendError(msg, code=code)
    return payload


# --- Credential resolution（只读 cfg["doubao"]）-------------------------------


def _doubao_cfg(cfg: dict) -> dict:
    return cfg.get("providers", {}).get("doubao") or {}


def _pick_auth(d: dict) -> str:
    """决定鉴权方式：auth 显式指定 > api_key > ak+sk > 报缺凭证。"""
    auth = d.get("auth")
    api_key = d.get("api_key")
    ak, sk = d.get("ak"), d.get("sk")
    if auth:
        if auth not in ("apikey", "aksk"):
            raise CredentialsError(
                f"无效鉴权方式 '{auth}'，请用 'apikey' 或 'aksk'", code="invalid_auth")
        return auth
    if api_key:
        return "apikey"
    if ak and sk:
        return "aksk"
    raise CredentialsError(MISSING_CRED_HINT, code="missing_credentials")


def _resolve_creds(d: dict) -> tuple[str, str, str]:
    """返回 (method, api_key, (ak, sk) 二选一凭据)。缺凭证抛 CredentialsError。"""
    method = _pick_auth(d)
    if method == "apikey":
        api_key = d.get("api_key")
        if not api_key:
            raise CredentialsError(
                "已选 apikey 鉴权但 doubao.api_key 为空", code="missing_credentials")
        return method, api_key, ""
    ak, sk = d.get("ak"), d.get("sk")
    if not (ak and sk):
        raise CredentialsError(
            "已选 aksk 鉴权但 doubao.ak / doubao.sk 未配置", code="missing_credentials")
    return method, ak, sk


def _has_credentials(cfg: dict) -> bool:
    """doubao 段有 api_key（Bearer）或有 ak+sk（签名）即 True。"""
    d = _doubao_cfg(cfg)
    return bool(d.get("api_key")) or (bool(d.get("ak")) and bool(d.get("sk")))


def _test_credentials(cfg: dict) -> str:
    """发一个最小搜索请求验证凭证，成功返回状态描述（如 "OK (0.4s)"）。"""
    t0 = time.monotonic()
    _request(cfg, "ping", {"count": 1, "timeout": 15})
    elapsed = time.monotonic() - t0
    return f"OK ({elapsed:.1f}s)"


# --- opts 合并规则 -----------------------------------------------------------
# 布尔项：仅当 opts 显式 True 才覆盖配置（False/None 不覆盖，配置优先）；
# 字符串/整型项：opts 非 None 才覆盖配置；否则取 cfg["doubao"] 对应键。

def _opt_bool(opts: dict, d: dict, key: str, default: bool = False) -> bool:
    v = opts.get(key)
    if v is True:
        return True
    return bool(d.get(key, default))


def _opt_str(opts: dict, d: dict, key: str) -> Any:
    v = opts.get(key)
    if v is not None:
        return v
    return d.get(key)


def _opt_int(opts: dict, d: dict, key: str) -> Any:
    v = opts.get(key)
    if v is not None:
        return v
    return d.get(key)


# --- API body 构造（参数映射与原 api.py 一致）--------------------------------


def _build_body(query: str, opts: dict, d: dict, image: bool) -> dict:
    if image:
        max_count, default_count = 5, int(d.get("count_image", 5))
        body: dict = {"Query": query, "SearchType": "image"}
    else:
        max_count, default_count = 50, int(d.get("count_web", 10))
        body = {"Query": query, "SearchType": "web"}
    count = opts.get("count")
    if count is None:
        count = default_count
    body["Count"] = max(1, min(int(count), max_count))

    filt: dict = {}
    if image:
        for key, field in (("width_min", "ImageWidthMin"), ("width_max", "ImageWidthMax"),
                           ("height_min", "ImageHeightMin"), ("height_max", "ImageHeightMax")):
            v = opts.get(key)
            if v is not None:
                filt[field] = v
        shapes = opts.get("shapes")
        if shapes:
            parts = [s for s in str(shapes).split("|") if s]
            if parts:
                filt["ImageShapes"] = parts
    else:
        if _opt_bool(opts, d, "need_content"):
            filt["NeedContent"] = True
        if _opt_bool(opts, d, "need_url"):
            filt["NeedUrl"] = True
        sites = _opt_str(opts, d, "sites")
        if sites:
            filt["Sites"] = sites
        block_hosts = _opt_str(opts, d, "block_hosts")
        if block_hosts:
            filt["BlockHosts"] = block_hosts
        auth_info_level = _opt_int(opts, d, "auth_info_level")
        if auth_info_level is not None:
            filt["AuthInfoLevel"] = auth_info_level
    if filt:
        body["Filter"] = filt

    if not image:
        time_range = _opt_str(opts, d, "time_range")
        if time_range:
            body["TimeRange"] = time_range
        content_formats = _opt_str(opts, d, "content_formats")
        if content_formats:
            body["ContentFormats"] = content_formats
        industry = _opt_str(opts, d, "industry")
        if industry:
            body["Industry"] = industry

    if _opt_bool(opts, d, "query_rewrite"):
        body["QueryControl"] = {"QueryRewrite": True}
    return body


def _request(cfg: dict, query: str, opts: dict) -> dict:
    """凭据解析 + body 构造 + 发送请求（apikey 或 aksk），返回原始 payload。"""
    d = _doubao_cfg(cfg)
    method, key1, key2 = _resolve_creds(d)
    timeout = opts.get("timeout")
    if timeout is None:
        timeout = d.get("timeout", 30)
    body = _build_body(query, opts, d, bool(opts.get("image")))
    if method == "apikey":
        headers = {
            "Authorization": f"Bearer {key1}",
            "Content-Type": "application/json",
        }
        return _do_request(APIKEY_URL, headers, json.dumps(body, ensure_ascii=False), timeout)
    return sign_and_send_aksk("POST", {}, body, key1, key2, "WebSearch", timeout)


# --- 结果转换 -----------------------------------------------------------------


def _to_results(payload: dict, image: bool) -> list[SearchResult]:
    result = payload.get("Result") or {}
    items = result.get("ImageResults" if image else "WebResults") or []
    out: list[SearchResult] = []
    for item in items:
        title = item.get("Title") or "(untitled)"
        url = item.get("Url") or ""
        if image:
            img = item.get("Image") or {}
            url = img.get("Url") or url
            snippet = ""
            extra: dict | None = None
            if img.get("Width") or img.get("Height") or img.get("Shape"):
                extra = {}
                if img.get("Width") is not None:
                    extra["width"] = img["Width"]
                if img.get("Height") is not None:
                    extra["height"] = img["Height"]
                if img.get("Shape"):
                    extra["shape"] = img["Shape"]
            if item.get("RankScore") is not None:
                extra = extra or {}
                extra["score"] = item["RankScore"]
        else:
            snippet = item.get("Summary") or item.get("Snippet") or ""
            extra = None
            if item.get("RankScore") is not None:
                extra = {"score": item["RankScore"]}
        out.append(SearchResult(
            title=title, url=url, snippet=snippet,
            content=item.get("Content") or None, extra=extra,
        ))
    return out


def _to_metadata(payload: dict, results: list[SearchResult]) -> dict:
    metadata: dict = {"total_results": len(results)}
    result = payload.get("Result") or {}
    for key in ("SearchTime", "Latency"):
        v = result.get(key)
        if isinstance(v, (int, float)):
            metadata["search_time_ms"] = int(v)
            break
    meta = payload.get("ResponseMetadata") or {}
    rid = meta.get("RequestId") or payload.get("RequestId")
    if rid:
        metadata["request_id"] = rid
    return metadata


# --- 对外接口 -----------------------------------------------------------------


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    """执行 doubao 搜索（image=True 走图片搜索，否则网页搜索）。"""
    query = (query or "").strip()
    # full 只影响展示（formatter 截断），不映射到 API body —— 与原 CLI 一致
    payload = _request(cfg, query, opts)

    results = _to_results(payload, bool(opts.get("image")))
    if not results:
        raise NoResultsError("未找到结果")
    return SearchResponse(
        query=query, results=results, answer=None, metadata=_to_metadata(payload, results),
    )


@register
class DoubaoProvider(Provider):
    """doubao 搜索后端（实现见上 *_search/_has_credentials/_test_credentials）。"""

    name = "doubao"
    capabilities = frozenset({"search"})
    search_params = {
        "image": ParamSpec(action="store_true", help="[doubao] 图片搜索"),
        "sites": ParamSpec(help="[doubao] 限定域名，| 分隔"),
        "block_hosts": ParamSpec(help="[doubao] 排除域名，| 分隔"),
        "time_range": ParamSpec(
            help="[doubao] OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD"
        ),
        "need_content": ParamSpec(action="store_true", help="[doubao] 只返回带正文的结果"),
        "need_url": ParamSpec(action="store_true", help="[doubao] 只返回带落地链接的结果"),
        "content_formats": ParamSpec(choices=("text", "markdown"), help="[doubao] 正文格式"),
        "industry": ParamSpec(choices=("finance", "game", "gov"), help="[doubao] 行业搜索"),
        "query_rewrite": ParamSpec(action="store_true", help="[doubao] 查询改写（更慢）"),
        "auth_info_level": ParamSpec(type=int, choices=(0, 1), help="[doubao] 1=仅高权威来源"),
        "width_min": ParamSpec(type=int, help="[doubao image] 最小宽度"),
        "width_max": ParamSpec(type=int, help="[doubao image] 最大宽度"),
        "height_min": ParamSpec(type=int, help="[doubao image] 最小高度"),
        "height_max": ParamSpec(type=int, help="[doubao image] 最大高度"),
        "shapes": ParamSpec(choices=("横长方形", "竖长方形", "方形"), help="[doubao image] 图片形状"),
    }

    def has_credentials(self, cfg: dict) -> bool:
        return _has_credentials(cfg)

    def test_credentials(self, cfg: dict) -> str:
        return _test_credentials(cfg)

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)
