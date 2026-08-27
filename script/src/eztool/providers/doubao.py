"""doubao（豆包/火山引擎 WebSearch）搜索后端。

两种鉴权（与官方文档一致）：
1. API Key (Bearer) — POST https://open.feedcoopapi.com/search_api/web_search
2. 火山引擎 AK/SK V4 签名 — POST https://mercury.volcengineapi.com?Action=WebSearch&Version=2025-01-01

纯标准库（hmac/hashlib/urllib 实现 V4 签名），无外部依赖。
从 doubao-websearch-cli 的 api.py 移植：签名与请求逻辑原样保留，CLI/配置管理不搬
（凭证统一走 providers.doubao 配置段，经 ProviderOpts.configs 注入 self.cfg）。

对外接口（eztool 主程序依赖）：
- has_credentials() -> bool
- test_credentials() -> str
- search(category, query, opts) -> SearchResponse
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import quote

from ..provider import (
    Provider,
    SearchResponse,
    SearchResult,
    post_json,
    register,
)
from ..util import (
    CATEGORY_HTTP,
    CredentialsError,
    NoResultsError,
    ServiceError,
)

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

MISSING_CRED_HINT = "doubao credentials not configured; run: eztool config set providers.doubao.api_key"

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
    ``post_json`` 同样用默认 ``json.dumps`` 序列化，字节一致，签名不受影响。
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
    return _do_request(url, headers, body, timeout)


# --- HTTP + response parsing（传输走 post_json，业务错误仍自行解析）------------


def _do_request(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """POST JSON（网络/HTTP 错误由 post_json 映射为 ServiceError）+ 业务错误解析。"""
    status, _resp_headers, body = post_json(url, headers, payload, timeout)
    return _parse_response(body.decode("utf-8", "replace"), status=status)


def _parse_response(raw: str, status: int | None = None) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ServiceError(
            f"non-JSON response (HTTP {status}): {raw[:200]}", CATEGORY_HTTP,
            code="bad_response") from None

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
        raise ServiceError(msg, CATEGORY_HTTP, code=code)
    return payload


# --- Credential resolution（只读 self.cfg，即 providers.doubao 段）-------------


def _pick_auth(d: dict) -> str:
    """决定鉴权方式：auth 显式指定 > api_key > ak+sk > 报缺凭证。"""
    auth = d.get("auth")
    api_key = d.get("api_key")
    ak, sk = d.get("ak"), d.get("sk")
    if auth:
        if auth not in ("apikey", "aksk"):
            raise CredentialsError(
                f"invalid auth method '{auth}'; use 'apikey' or 'aksk'", code="invalid_auth")
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
                "auth 'apikey' selected but providers.doubao.api_key is empty",
                code="missing_credentials")
        return method, api_key, ""
    ak, sk = d.get("ak"), d.get("sk")
    if not (ak and sk):
        raise CredentialsError(
            "auth 'aksk' selected but providers.doubao.ak / providers.doubao.sk "
            "are not configured",
            code="missing_credentials")
    return method, ak, sk


# --- opts 合并规则 -----------------------------------------------------------
# 布尔项：仅当 opts 显式 True 才覆盖配置（False/None 不覆盖，配置优先）；
# 字符串/整型项：opts 非 None 才覆盖配置；否则取 self.cfg 对应键。

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


# --- API body 构造（参数映射与原 api.py 一致）--------------------------------


def _build_body(query: str, opts: dict, d: dict) -> dict:
    body: dict = {"Query": query, "SearchType": "web"}
    # 不传 Count → 服务端默认条数；opts["count"] 或 count_web 配置显式覆盖
    count = opts.get("count")
    if count is None:
        count = d.get("count_web")
    if count is not None:
        body["Count"] = max(1, min(int(count), 50))

    filt: dict = {}
    if _opt_bool(opts, d, "need_content"):
        filt["NeedContent"] = True
    if _opt_bool(opts, d, "need_url"):
        filt["NeedUrl"] = True
    if filt:
        body["Filter"] = filt

    time_range = _opt_str(opts, d, "time_range")
    if time_range:
        body["TimeRange"] = time_range
    content_formats = _opt_str(opts, d, "content_formats")
    if content_formats:
        body["ContentFormats"] = content_formats
    industry = _opt_str(opts, d, "industry")
    if industry:
        body["Industry"] = industry
    return body


# --- 结果转换 -----------------------------------------------------------------


def _to_results(payload: dict) -> list[SearchResult]:
    result = payload.get("Result") or {}
    items = result.get("WebResults") or []
    out: list[SearchResult] = []
    for item in items:
        title = item.get("Title") or "(untitled)"
        url = item.get("Url") or ""
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


# --- Provider 实现 ------------------------------------------------------------


@register
class DoubaoProvider(Provider):
    """doubao 搜索后端（API Key Bearer 或火山引擎 AK/SK 签名）。"""

    name = "doubao"
    categories = frozenset({"web"})
    # 配置键（自动生成 config show/set 的键、默认值、secret 脱敏、提示）
    config = {
        "api_key": {"secret": True, "hint": "Doubao WebSearch API key (Bearer)"},
        "ak": {"secret": True, "hint": "Volcengine AccessKey"},
        "sk": {"secret": True, "hint": "Volcengine SecretKey"},
        "auth": {"hint": "auth method: apikey / aksk (leave empty to auto-detect)"},
        "count_web": {"hint": "web result count (1-50); unset = server default"},
        "need_url": {"default": False, "hint": "only return results with landing URLs (true/false)"},
        "need_content": {"default": False, "hint": "only return results with full content (true/false)"},
        "content_formats": {"hint": "content format: text / markdown"},
        "time_range": {"hint": "time range: OneDay/OneWeek/OneMonth/OneYear or YYYY-MM-DD..YYYY-MM-DD"},
        "industry": {"hint": "industry search: finance / game / gov"},
    }
    priority = {"web": 20}  # 默认链排序（小在前）
    auth_required = True  # 必须有 apikey 或 AK/SK 才能用（链跳过未配的）

    def has_credentials(self) -> bool:
        """doubao 段有 api_key（Bearer）或有 ak+sk（签名）即 True。"""
        d = self.cfg
        return bool(d.get("api_key")) or (bool(d.get("ak")) and bool(d.get("sk")))

    def test_credentials(self) -> str:
        """发一个最小搜索请求验证凭证，成功返回状态描述（如 "OK (0.4s)"）。"""
        t0 = time.monotonic()
        self._request("ping", {"count": 1}, timeout=15)
        elapsed = time.monotonic() - t0
        return f"OK ({elapsed:.1f}s)"

    def search(self, category: str, query: str, opts: dict) -> SearchResponse:
        """执行 doubao 网页搜索。"""
        query = (query or "").strip()
        payload = self._request(query, opts, timeout=self.timeout())

        results = _to_results(payload)
        if not results:
            raise NoResultsError("no results found")
        return SearchResponse(
            query=query, results=results, answer=None,
            metadata=_to_metadata(payload, results),
        )

    def _request(self, query: str, opts: dict, timeout: float) -> dict:
        """凭据解析 + body 构造 + 发送请求（apikey 或 aksk），返回原始 payload。"""
        d = self.cfg
        method, key1, key2 = _resolve_creds(d)
        body = _build_body(query, opts, d)
        if method == "apikey":
            headers = {"Authorization": f"Bearer {key1}"}
            return _do_request(APIKEY_URL, headers, body, timeout)
        return sign_and_send_aksk("POST", {}, body, key1, key2, "WebSearch", timeout)
