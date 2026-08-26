"""公共工具：异常体系 + HTTP 请求 + 内容质量门（合并自 errors/http/quality）。

所有网络错误统一映射为 ServiceError（带 machine-readable category），
回退链 / 并行 fan-out 据此决策。质量门拦截"假成功"（HTTP 200 + 反爬/验证页）。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

# ── 错误分类（machine-readable，链/并行决策依据）──────────────────────────
CATEGORY_TIMEOUT = "timeout"
CATEGORY_NETWORK = "network"
CATEGORY_HTTP = "http"
CATEGORY_EMPTY = "empty"
CATEGORY_BLOCKED = "blocked"  # 内容疑似拦截/验证页（假成功拦截）
CATEGORY_INVALID = "invalid"
CATEGORY_AUTH = "auth"
CATEGORY_NO_RESULTS = "no_results"
CATEGORY_ALL_FAILED = "all_failed"

# 不可重试分类：换 provider 也没有意义
NON_RETRIABLE = frozenset({CATEGORY_INVALID, CATEGORY_AUTH, CATEGORY_NO_RESULTS})


class EztoolError(Exception):
    """所有 eztool 错误的基类。"""

    exit_code = 1

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


class ServiceError(EztoolError):
    """服务商调用失败（fetch / search / convert 通用）。"""

    exit_code = 1

    def __init__(
        self,
        message: str,
        category: str = CATEGORY_HTTP,
        http_code: int | None = None,
        code: str | None = None,
    ):
        super().__init__(message, code)
        self.category = category
        self.http_code = http_code

    @property
    def retriable(self) -> bool:
        """True = 换下一个 provider 重试有意义。"""
        return self.category not in NON_RETRIABLE

    def __str__(self) -> str:  # compact one-line form for chain stderr logs
        if self.http_code is not None and f"HTTP {self.http_code}" not in self.message:
            return f"{self.message} (HTTP {self.http_code})"
        return self.message


class UsageError(EztoolError):
    """参数用法错误（参数不属于当前后端等）。"""

    exit_code = 2


class CredentialsError(ServiceError):
    """凭证缺失或无效。"""

    exit_code = 2

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message, CATEGORY_AUTH, code=code)


class NoResultsError(ServiceError):
    """搜索无结果。"""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message, CATEGORY_NO_RESULTS, code=code)


# ── HTTP ───────────────────────────────────────────────────────────────────

USER_AGENT = "Mozilla/5.0 (compatible; eztool/0.5)"


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


def post_json(target: str, headers: dict, payload, timeout: int):
    """POST JSON 到 ``target``；返回 (status, headers, body bytes)。

    收敛各 provider 的「序列化 + Content-Type + POST」样板；错误同样走
    ``map_http_error`` 映射为 ServiceError。
    """
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    return http_post(target, h, data, timeout)


def _urlopen(req, timeout: int):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except Exception as e:
        raise map_http_error(e, timeout) from e


def map_http_error(e: Exception, timeout: int) -> ServiceError:
    """把 stdlib HTTP/网络异常映射到 ServiceError 分类。

    HTTPError 会尽力从错误体解析 JSON（如 {"error": "..."}），让链日志
    带上服务端原因。
    """
    if isinstance(e, urllib.error.HTTPError):
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            if isinstance(payload, dict):
                # 常见错误信封：{"error": ...} / {"msg": ...} / {"error": {"message": ...}}
                val = payload.get("error") or payload.get("msg") or payload.get("message")
                if isinstance(val, dict):
                    val = val.get("message") or val.get("msg")
                if val:
                    detail = f": {val}"
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

    纯 ASCII 原样返回；已有 ``%XX`` 转义保留（不重复编码）。
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
    """构建单文件 ``multipart/form-data`` 请求体。

    返回 ``(body, content-type header value)``。文件转换类 provider 共用。
    """
    boundary = "----eztool" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body = head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


# ── 内容质量门 ─────────────────────────────────────────────────────────────
# 拦截页必须告诉用户"你被拦了"，因此内容开头（标题+首段）必然命中通用拦截话术，
# 且拦截页几乎没有正文。前缀命中 + 内容短 = 拦截/可疑；内容充足 = 信任。

HEAD_WINDOW = 200    # 检测窗口：只扫标题 + 开头
BLOCK_LIMIT = 800    # 低于此 + 命中拦截词 = 拦截页
WARN_LIMIT = 1500    # 低于此 + 命中拦截词 = 可疑（返回但警告）

STRONG_WORDS = (
    # 中文（微信公众号/主流验证页）
    "环境异常", "完成验证", "去验证", "人机验证", "访问过于频繁",
    "请求过于频繁", "安全验证", "验证后即可", "滑动验证",
    # 英文（Cloudflare / 主流 WAF）
    "captcha", "just a moment", "attention required", "verify you are human",
    "are you a human", "checking your browser", "security check",
    "access denied", "enable javascript and cookies",
)


@dataclass
class ContentQuality:
    """质量判定结果。ok=False = 拦截页（调用方应抛错继续回退链）。"""

    ok: bool = True
    low_quality: bool = False
    hits: list[str] = field(default_factory=list)


def assess_content(text: str) -> ContentQuality:
    """判定抓取内容是否为拦截页/可疑内容。"""
    head = (text or "")[:HEAD_WINDOW].lower()
    hits = [w for w in STRONG_WORDS if w in head]
    if not hits:
        return ContentQuality(ok=True, low_quality=False)

    length = len((text or "").strip())
    if length < BLOCK_LIMIT:
        return ContentQuality(ok=False, low_quality=False, hits=hits)
    if length < WARN_LIMIT:
        return ContentQuality(ok=True, low_quality=True, hits=hits)
    return ContentQuality(ok=True, low_quality=False, hits=hits)


def checked_text(provider: str, text: str) -> tuple[bool, str]:
    """质量门（fetch 路径统一收口）：拦截页抛 ServiceError(CATEGORY_BLOCKED)。

    返回 ``(low_quality, reason)``；reason 为命中的拦截词（log 用）。
    """
    q = assess_content(text)
    if not q.ok:
        raise ServiceError(
            f"{provider} content looks like a bot-check/interstitial page "
            f"(hits: {', '.join(q.hits)}, {len((text or '').strip())} chars)",
            CATEGORY_BLOCKED,
        )
    return q.low_quality, ", ".join(q.hits)
