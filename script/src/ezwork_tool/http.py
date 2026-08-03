"""纯标准库 HTTP 工具：请求、错误映射、multipart、URL 编码。

所有网络错误统一映射为 ServiceError（带 category），供回退链决策。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid

from .errors import (
    CATEGORY_HTTP,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    ServiceError,
)

USER_AGENT = "Mozilla/5.0 (compatible; ezwork-fetch/0.1)"


def http_get(target: str, headers: dict, timeout: int):
    """GET ``target``；返回 (status, headers, body bytes)。错误映射为 ServiceError。"""
    req = urllib.request.Request(target, headers=headers)
    return _urlopen(req, timeout)


def http_post(target: str, headers: dict, data: bytes, timeout: int):
    """POST ``data`` 到 ``target``；返回 (status, headers, body bytes)。"""
    req = urllib.request.Request(target, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    return _urlopen(req, timeout)


def _urlopen(req, timeout: int):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except Exception as e:
        raise map_http_error(e, timeout) from e


def map_http_error(e: Exception, timeout: int) -> ServiceError:
    """把 stdlib HTTP/网络异常映射到 ServiceError 分类。

    HTTPError 会尽力从错误体解析 JSON（如 {"error": "..."}），让回退链日志
    带上服务端原因。
    """
    if isinstance(e, urllib.error.HTTPError):
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            if isinstance(payload, dict) and payload.get("error"):
                detail = f": {payload['error']}"
        except Exception:
            pass
        return ServiceError(
            f"HTTP {e.code}{detail}", CATEGORY_HTTP, http_code=e.code
        )
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", e)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return ServiceError(f"timed out after {timeout}s", CATEGORY_TIMEOUT)
        return ServiceError(f"connection failed: {reason}", CATEGORY_NETWORK)
    if isinstance(e, (TimeoutError, socket.timeout)):
        return ServiceError(f"timed out after {timeout}s", CATEGORY_TIMEOUT)
    return ServiceError(f"{type(e).__name__}: {e}", CATEGORY_NETWORK)


def ensure_ascii(url: str) -> str:
    """Percent-encode non-ASCII characters in a URL.

    Pure-ASCII URLs are returned unchanged. Existing ``%XX`` escapes are
    preserved, so already-encoded URLs never get double-encoded. Callers
    can pass raw URLs (e.g. ``https://zh.wikipedia.org/wiki/人工智能``)
    and get a request-line-safe target.
    """
    try:
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass
    out = []
    i = 0
    n = len(url)
    while i < n:
        ch = url[i]
        if ch == "%" and i + 2 < n:
            hexpart = url[i + 1:i + 3]
            if all(c in "0123456789abcdefABCDEF" for c in hexpart):
                out.append(url[i:i + 3])  # keep existing escape
                i += 3
                continue
        if ord(ch) > 127:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def build_multipart(
    field: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str]:
    """Build a single-file ``multipart/form-data`` body.

    Returns ``(body, content-type header value)``. Shared by every
    file-conversion provider (each POSTs the local file the same way).
    """
    boundary = "----ezwork" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body = head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"
