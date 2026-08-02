"""mineru.net provider — MinerU Agent Lightweight Extract API (document → Markdown).

Two capabilities, one free service (no login, no Token; IP rate-limited):
  - convert_file: POST /api/v1/agent/parse/file (signed PUT upload) → poll → Markdown
  - fetch:        POST /api/v1/agent/parse/url  (remote file URL)   → poll → Markdown

Async flow for both modes: submit task → (upload) → poll
GET /api/v1/agent/parse/{task_id} → when state=done download markdown_url.

Limits: 10 MB per file, 20 pages, single file per request. Formats: pdf,
png/jpg/jpeg/jp2/webp/gif/bmp, docx, pptx, xlsx. Markdown output only.

This is the lightweight public API. The Precision Extract API
(/api/v4/*, Bearer Token, 200MB, batch, zip output incl. JSON) is not
implemented — the lightweight one needs no credentials and returns
Markdown directly, which fits the chain's contract.

Service docs: https://mineru.net/apiManage/docs
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from ..provider import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CATEGORY_TIMEOUT,
    FetchError,
    FetchResult,
    Provider,
    register,
)

BASE_URL = "https://mineru.net"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB service limit

# Server-authoritative list (lightweight API; note: doc/ppt/xls not included).
SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
    ".docx", ".pptx", ".xlsx",
})

POLL_INTERVAL = 3  # seconds between status polls
_POLL_STATES = {"waiting-file", "uploading", "pending", "running"}
# Submission accepted but the request itself was wrong — not retriable.
_INVALID_CODES = {-30001, -30002, -30003, -30004}


@register
class MinerUProvider(Provider):
    name = "mineru"

    # -- convert_file: local file → Markdown -------------------------------

    def convert_file(self, path: str, timeout: int = 300) -> FetchResult:
        """Signed-upload a local file and return the extracted Markdown.

        ``timeout`` is the overall budget (submit + upload + poll +
        download); extraction is asynchronous so this is not a single
        HTTP timeout.
        """
        if not os.path.isfile(path):
            raise FetchError(f"file not found: {path}", CATEGORY_INVALID)
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise FetchError(
                f"unsupported file type '{ext}' (supported: {supported})",
                CATEGORY_INVALID,
            )
        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            raise FetchError(
                f"file too large: {size} bytes (max {MAX_FILE_SIZE})", CATEGORY_INVALID
            )

        t0 = time.monotonic()

        # 1. Request a signed OSS upload URL for this file.
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v1/agent/parse/file",
            {"file_name": os.path.basename(path)},
            timeout=min(30, timeout),
        )
        data = self._parse_task_response(raw)
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise FetchError(
                f"{self.name} response missing task_id/file_url", CATEGORY_EMPTY
            )

        # 2. PUT the raw bytes to the signed URL.
        # OSS 签名校验要求请求不带 Content-Type；urllib 在带 data 时会自动补
        # application/x-www-form-urlencoded，导致 SignatureDoesNotMatch (403)。
        # 显式加一个空值 Content-Type 头可阻止 urllib 补默认头（OSS 视空值为无）。
        with open(path, "rb") as f:
            file_bytes = f.read()
        req = urllib.request.Request(file_url, data=file_bytes, method="PUT")
        req.add_header("User-Agent", self.build_headers().get("User-Agent", ""))
        req.add_header("Content-Type", "")
        try:
            with urllib.request.urlopen(req, timeout=min(60, timeout)) as resp:
                put_status = resp.status
        except Exception as e:
            raise self._map_error(e, min(60, timeout)) from e
        if put_status not in (200, 201):
            raise FetchError(
                f"upload failed: HTTP {put_status}", CATEGORY_HTTP, put_status
            )

        # 3. Poll until done/failed, then download the Markdown.
        content = self._poll_and_download(task_id, timeout=timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=path,
            elapsed=round(time.monotonic() - t0, 3),
        )

    # -- fetch: remote file URL → Markdown ---------------------------------

    def fetch(self, url: str, timeout: int = 300) -> FetchResult:
        """Submit a remote file URL for extraction and return the Markdown.

        Overrides the base synchronous fetch: the service is async
        (submit → poll → download), so ``timeout`` is the overall budget.
        Only file URLs (PDF/Office/images) are supported; HTML pages are
        rejected by the service (err -30002) and the chain moves on.
        """
        t0 = time.monotonic()
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v1/agent/parse/url", {"url": url}, timeout=min(30, timeout)
        )
        data = self._parse_task_response(raw)
        task_id = data.get("task_id")
        if not task_id:
            raise FetchError(f"{self.name} response missing task_id", CATEGORY_EMPTY)

        content = self._poll_and_download(task_id, timeout=timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=url,
            elapsed=round(time.monotonic() - t0, 3),
        )

    # -- shared helpers -----------------------------------------------------

    def _post_json(self, target: str, payload: dict, timeout: int):
        """POST JSON body; returns (status, headers, body bytes)."""
        req = urllib.request.Request(
            target, data=json.dumps(payload).encode("utf-8"), method="POST"
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", self.build_headers().get("User-Agent", ""))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except Exception as e:
            raise self._map_error(e, timeout) from e

    def _parse_task_response(self, raw: bytes) -> dict:
        """Validate the {code, msg, data} envelope; return data."""
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise FetchError(f"{self.name} returned invalid JSON", CATEGORY_EMPTY) from None
        if not isinstance(payload, dict):
            raise FetchError(f"{self.name} returned invalid JSON", CATEGORY_EMPTY)
        if payload.get("code") != 0:
            code = payload.get("code")
            category = CATEGORY_INVALID if code in _INVALID_CODES else CATEGORY_HTTP
            raise FetchError(
                payload.get("msg") or f"request failed (code={code})", category
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FetchError(f"{self.name} response missing data", CATEGORY_EMPTY)
        return data

    def _poll_and_download(self, task_id: str, timeout: int) -> str:
        """Poll the task state within the budget; download Markdown when done."""
        deadline = time.monotonic() + max(timeout, 1)

        def _remaining() -> float:
            return deadline - time.monotonic()

        while True:
            if _remaining() <= 0:
                raise FetchError(
                    f"timed out after {int(timeout)}s waiting for extract task",
                    CATEGORY_TIMEOUT,
                )
            status, _, raw = self._http_get(
                f"{BASE_URL}/api/v1/agent/parse/{task_id}",
                timeout=min(20.0, _remaining()),
            )
            data = self._parse_task_response(raw)
            state = data.get("state")

            if state == "done":
                if _remaining() <= 0:
                    raise FetchError(
                        f"timed out after {int(timeout)}s waiting for extract task",
                        CATEGORY_TIMEOUT,
                    )
                md_url = data.get("markdown_url")
                if not md_url:
                    raise FetchError(
                        f"{self.name} task done but no markdown_url", CATEGORY_EMPTY
                    )
                status, _, body = self._http_get(
                    md_url, timeout=min(20.0, _remaining())
                )
                text = body.decode("utf-8", errors="replace").strip()
                if not text:
                    raise FetchError(
                        f"{self.name} returned empty content", CATEGORY_EMPTY
                    )
                return text

            if state == "failed":
                raise FetchError(
                    data.get("err_msg") or "extract task failed", CATEGORY_HTTP
                )

            if state not in _POLL_STATES:
                raise FetchError(f"unknown task state '{state}'", CATEGORY_HTTP)
            time.sleep(min(POLL_INTERVAL, max(0.0, _remaining())))
