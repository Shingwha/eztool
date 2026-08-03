"""markdown.new provider — Cloudflare's file/URL→Markdown service.

Two capabilities, same service:
  - fetch:         GET https://markdown.new/<url>         URL → Markdown
  - convert_file:  POST https://markdown.new/convert      local file → Markdown

No API key required. Limits: 10 MB per file, 500 requests/day per IP,
files are processed in memory and never stored. Supported extensions
(server-authoritative list, 22 formats): .txt .md .csv .json .html
.htm .xml .pdf .docx .odt .xlsx .xlsm .xlsb .xls .et .ods .numbers
.jpeg .jpg .png .webp .svg. Images go through AI vision (object
detection + summarization).
"""
from __future__ import annotations

import json
import os
import urllib.request

from ..base import FetchResult, Provider
from ..errors import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    ServiceError,
)
from ..http import build_multipart, map_http_error
from ..registry import register

CONVERT_URL = "https://markdown.new/convert"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB service limit

# Server-authoritative list (mirrors the service's own UNSUPPORTED_FORMAT error).
SUPPORTED_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml",
    ".pdf", ".docx", ".odt", ".xlsx", ".xlsm", ".xlsb", ".xls",
    ".et", ".ods", ".numbers", ".jpeg", ".jpg", ".png", ".webp", ".svg",
})

# Service error codes that mean "the request itself is wrong" — not retriable.
_INVALID_CODES = {"UNSUPPORTED_FORMAT", "FILE_TOO_LARGE", "INVALID_FILE"}


@register
class MarkdownNewProvider(Provider):
    name = "markdown"
    capabilities = frozenset({"fetch", "convert_file"})
    base_url = "https://markdown.new/"

    # Note: markdown.new treats the whole request path+query as the target
    # URL, so we cannot append ?method= without corrupting URLs that carry
    # their own query string. method (auto|ai|browser) is therefore left at
    # the service default (auto, its built-in 3-tier fallback).

    def convert_file(self, path: str, timeout: int = 60) -> FetchResult:
        """Upload a local file to /convert and return the Markdown.

        Local pre-checks (exists / size / extension) fail fast with
        CATEGORY_INVALID so bad input never burns a request or a chain slot.
        """
        if not os.path.isfile(path):
            raise ServiceError(f"file not found: {path}", CATEGORY_INVALID)
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ServiceError(
                f"unsupported file type '{ext}' (supported: {supported})",
                CATEGORY_INVALID,
            )
        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            raise ServiceError(
                f"file too large: {size} bytes (max {MAX_FILE_SIZE})", CATEGORY_INVALID
            )

        with open(path, "rb") as f:
            body, content_type = build_multipart(
                "file", os.path.basename(path), f.read()
            )
        req = urllib.request.Request(CONVERT_URL, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        req.add_header("User-Agent", self.build_headers().get("User-Agent", ""))

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, headers, raw = resp.status, resp.headers, resp.read()
        except Exception as e:  # HTTP/network errors → standard taxonomy
            if isinstance(e, urllib.error.HTTPError):
                # 服务端错误 JSON 的语义码（UNSUPPORTED_FORMAT 等）→ INVALID，链不重试。
                # body 只读一次：解析出 code/error 后直接构造 ServiceError（带服务端原因）。
                code, msg = "", None
                try:
                    payload = json.loads(e.read().decode("utf-8", "replace"))
                    code, msg = payload.get("code", ""), payload.get("error")
                except Exception:
                    pass
                category = CATEGORY_INVALID if code in _INVALID_CODES else CATEGORY_HTTP
                detail = f": {msg}" if msg else ""
                raise ServiceError(f"HTTP {e.code}{detail}", category, e.code) from e
            raise map_http_error(e, timeout) from e

        if status != 200:
            raise ServiceError(f"{self.name} returned HTTP {status}", CATEGORY_HTTP, status)

        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise ServiceError(f"{self.name} returned invalid JSON", CATEGORY_EMPTY) from None

        if not isinstance(payload, dict) or not payload.get("success"):
            code = payload.get("code", "") if isinstance(payload, dict) else ""
            msg = payload.get("error") if isinstance(payload, dict) else None
            category = CATEGORY_INVALID if code in _INVALID_CODES else CATEGORY_HTTP
            raise ServiceError(msg or f"conversion failed (code={code or 'unknown'})", category)

        data = payload.get("data") or {}
        content = data.get("content") or ""
        if not content.strip():
            raise ServiceError(f"{self.name} returned empty content", CATEGORY_EMPTY)

        tokens = data.get("tokens")
        return FetchResult(
            provider=self.name,
            content=content,
            url=path,
            elapsed=0.0,
            tokens=tokens if isinstance(tokens, int) else None,
        )
