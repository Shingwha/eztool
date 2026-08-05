"""mineru.net provider — MinerU document extraction (PDF/Office/images → Markdown).

Two API tiers behind one provider, selected automatically by token:

  - v1 Agent Lightweight API (no token): free, IP rate-limited, ≤10 MB / 20
    pages, single file, Markdown only. Formats: pdf, images, docx, pptx, xlsx.
  - v4 Precision Extract API (Bearer token): ≤200 MB / 200 pages, batch,
    output is a zip (Markdown + JSON). Formats: pdf, doc/docx, ppt/pptx,
    xls/xlsx, images, html (html needs model MinerU-HTML).

Configure ``mineru.api_key`` (convert.mineru.api_key / fetch.mineru.api_key)
to enable v4; without a token the provider falls back to v1.

Async flow (both tiers): submit task → (signed PUT upload) → poll status →
download result (v1: markdown_url; v4: zip → extract full.md).

Service docs: https://mineru.net/apiManage/docs
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.request
import zipfile

from ..base import FetchResult, Provider
from ..errors import (
    CATEGORY_EMPTY,
    CATEGORY_HTTP,
    CATEGORY_INVALID,
    CATEGORY_TIMEOUT,
    ServiceError,
)
from ..http import http_get, http_post, map_http_error
from ..quality import checked_text
from ..registry import register

BASE_URL = "https://mineru.net"
V1_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
V4_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB

# Server-authoritative lists (note: v1 does not include doc/ppt/xls/html).
V1_SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
    ".docx", ".pptx", ".xlsx",
})
V4_SUPPORTED_EXTENSIONS = V1_SUPPORTED_EXTENSIONS | frozenset({
    ".doc", ".ppt", ".xls", ".html",
})

POLL_INTERVAL = 3  # seconds between status polls
_POLL_STATES = {"waiting-file", "uploading", "pending", "running", "converting"}
# Submission accepted but the request itself was wrong — not retriable.
_INVALID_CODES = {-30001, -30002, -30003, -30004, -500, -10002}


@register
class MinerUProvider(Provider):
    name = "mineru"
    categories = frozenset({"convert.page", "convert.file"})

    @property
    def _v4(self) -> bool:
        """带 token 走 v4 Precision API；不带 token 走 v1 Agent 轻量 API。"""
        return bool(self.api_key)

    # -- convert_file: local file → Markdown -------------------------------

    def convert_file(self, path: str, timeout: int = 300) -> FetchResult:
        """Signed-upload a local file and return the extracted Markdown.

        ``timeout`` is the overall budget (submit + upload + poll +
        download); extraction is asynchronous so this is not a single
        HTTP timeout.
        """
        ext = self._check_local_file(path)
        if self._v4:
            return self._convert_v4(path, ext, timeout)
        return self._convert_v1(path, timeout)

    # -- fetch: remote file URL → Markdown ---------------------------------

    def fetch(self, url: str, timeout: int = 300) -> FetchResult:
        """Submit a remote file URL for extraction and return the Markdown.

        Overrides the base synchronous fetch: the service is async
        (submit → poll → download), so ``timeout`` is the overall budget.
        v1 only accepts file URLs (PDF/Office/images); v4 additionally
        accepts HTML URLs (model MinerU-HTML). Other URLs are rejected by
        the service and the chain moves on.
        """
        if self._v4:
            result = self._fetch_v4(url, timeout)
        else:
            result = self._fetch_v1(url, timeout)
        # 质量门：拦截"假成功"（远程 URL 提取返回拦截页/异常文档）
        low, reason = checked_text(self.name, result.content)
        result.low_quality, result.quality_reason = low, reason
        return result

    # -- v1: Agent Lightweight API (no token) -------------------------------

    def _convert_v1(self, path: str, timeout: int) -> FetchResult:
        t0 = time.monotonic()

        # 1. Request a signed OSS upload URL for this file.
        # is_ocr=True：扫描件/图片型 PDF 也做 OCR 识别（仅对 PDF 生效）。
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v1/agent/parse/file",
            {"file_name": os.path.basename(path), "is_ocr": True},
            timeout=min(30, timeout),
        )
        data = self._parse_task_response(raw)
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise ServiceError(
                f"{self.name} response missing task_id/file_url", CATEGORY_EMPTY
            )

        self._put_file(file_url, path, timeout)

        done = self._poll_status(
            f"{BASE_URL}/api/v1/agent/parse/{task_id}", timeout
        )
        content = self._download_text(done.get("markdown_url"), timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=path,
            elapsed=round(time.monotonic() - t0, 3),
        )

    def _fetch_v1(self, url: str, timeout: int) -> FetchResult:
        t0 = time.monotonic()
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v1/agent/parse/url",
            {"url": url, "is_ocr": True},
            timeout=min(30, timeout),
        )
        data = self._parse_task_response(raw)
        task_id = data.get("task_id")
        if not task_id:
            raise ServiceError(f"{self.name} response missing task_id", CATEGORY_EMPTY)

        done = self._poll_status(
            f"{BASE_URL}/api/v1/agent/parse/{task_id}", timeout
        )
        content = self._download_text(done.get("markdown_url"), timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=url,
            elapsed=round(time.monotonic() - t0, 3),
        )

    # -- v4: Precision Extract API (Bearer token) ---------------------------

    def _convert_v4(self, path: str, ext: str, timeout: int) -> FetchResult:
        t0 = time.monotonic()

        # 1. Request a signed OSS upload URL (batch endpoint, one file).
        # is_ocr=True 默认开启 OCR；HTML 源文件必须用 MinerU-HTML 模型。
        payload = {
            "files": [{"name": os.path.basename(path), "is_ocr": True}],
            "model_version": "MinerU-HTML" if ext == ".html" else "vlm",
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
        }
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v4/file-urls/batch", payload, timeout=min(30, timeout)
        )
        data = self._parse_task_response(raw)
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if not batch_id or not isinstance(file_urls, list) or not file_urls:
            raise ServiceError(
                f"{self.name} response missing batch_id/file_urls", CATEGORY_EMPTY
            )

        # 2. Upload; the service auto-submits the extract task afterwards.
        self._put_file(file_urls[0], path, timeout)

        # 3. Poll the batch result; download the zip and extract full.md.
        done = self._poll_status(
            f"{BASE_URL}/api/v4/extract-results/batch/{batch_id}", timeout
        )
        results = done.get("extract_result") or []
        zip_url = results[0].get("full_zip_url") if results else None
        if not zip_url:
            raise ServiceError(
                f"{self.name} result missing full_zip_url", CATEGORY_EMPTY
            )
        content = self._download_zip_markdown(zip_url, timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=path,
            elapsed=round(time.monotonic() - t0, 3),
        )

    def _fetch_v4(self, url: str, timeout: int) -> FetchResult:
        t0 = time.monotonic()

        # HTML URLs need the MinerU-HTML model; everything else uses vlm.
        model = "MinerU-HTML" if url.lower().split("?", 1)[0].endswith(
            (".html", ".htm")
        ) else "vlm"
        payload = {
            "url": url,
            "model_version": model,
            "is_ocr": True,
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
        }
        _, _, raw = self._post_json(
            f"{BASE_URL}/api/v4/extract/task", payload, timeout=min(30, timeout)
        )
        data = self._parse_task_response(raw)
        task_id = data.get("task_id")
        if not task_id:
            raise ServiceError(f"{self.name} response missing task_id", CATEGORY_EMPTY)

        done = self._poll_status(
            f"{BASE_URL}/api/v4/extract/task/{task_id}", timeout
        )
        content = self._download_zip_markdown(done.get("full_zip_url"), timeout)
        return FetchResult(
            provider=self.name,
            content=content,
            url=url,
            elapsed=round(time.monotonic() - t0, 3),
        )

    # -- shared helpers -----------------------------------------------------

    def _check_local_file(self, path: str) -> str:
        """Local pre-checks for the active tier; returns the extension."""
        if not os.path.isfile(path):
            raise ServiceError(f"file not found: {path}", CATEGORY_INVALID)
        ext = os.path.splitext(path)[1].lower()
        supported = V4_SUPPORTED_EXTENSIONS if self._v4 else V1_SUPPORTED_EXTENSIONS
        if ext not in supported:
            raise ServiceError(
                f"unsupported file type '{ext}' (supported: "
                f"{', '.join(sorted(supported))})",
                CATEGORY_INVALID,
            )
        max_size = V4_MAX_FILE_SIZE if self._v4 else V1_MAX_FILE_SIZE
        size = os.path.getsize(path)
        if size > max_size:
            raise ServiceError(
                f"file too large: {size} bytes (max {max_size})", CATEGORY_INVALID
            )
        return ext

    def _auth_headers(self) -> dict:
        """Common headers; v4 adds the Bearer token."""
        headers = {"User-Agent": self.build_headers().get("User-Agent", "")}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post_json(self, target: str, payload: dict, timeout: int):
        """POST JSON body; returns (status, headers, body bytes)."""
        headers = {"Content-Type": "application/json", **self._auth_headers()}
        return http_post(target, headers, json.dumps(payload).encode("utf-8"), timeout)

    def _get_json(self, target: str, timeout: float) -> bytes:
        """Authorized GET (Bearer when v4); returns raw body bytes."""
        return http_get(target, self._auth_headers(), timeout)[2]

    def _put_file(self, url: str, path: str, timeout: int) -> None:
        """PUT the file bytes to the signed OSS URL.

        OSS 签名校验要求请求不带 Content-Type；urllib 在带 data 时会自动补
        application/x-www-form-urlencoded，导致 SignatureDoesNotMatch (403)。
        显式加一个空值 Content-Type 头可阻止 urllib 补默认头（OSS 视空值为无）。
        """
        with open(path, "rb") as f:
            file_bytes = f.read()
        req = urllib.request.Request(url, data=file_bytes, method="PUT")
        req.add_header("User-Agent", self.build_headers().get("User-Agent", ""))
        req.add_header("Content-Type", "")
        try:
            with urllib.request.urlopen(req, timeout=min(60, timeout)) as resp:
                put_status = resp.status
        except Exception as e:
            raise map_http_error(e, min(60, timeout)) from e
        if put_status not in (200, 201):
            raise ServiceError(
                f"upload failed: HTTP {put_status}", CATEGORY_HTTP, put_status
            )

    def _parse_task_response(self, raw: bytes) -> dict:
        """Validate the {code, msg, data} envelope; return data."""
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise ServiceError(f"{self.name} returned invalid JSON", CATEGORY_EMPTY) from None
        if not isinstance(payload, dict):
            raise ServiceError(f"{self.name} returned invalid JSON", CATEGORY_EMPTY)
        if payload.get("code") != 0:
            code = payload.get("code")
            category = CATEGORY_INVALID if code in _INVALID_CODES else CATEGORY_HTTP
            raise ServiceError(
                payload.get("msg") or f"request failed (code={code})", category
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ServiceError(f"{self.name} response missing data", CATEGORY_EMPTY)
        return data

    def _poll_status(self, status_url: str, timeout: int) -> dict:
        """Poll until done/failed; returns the final data dict.

        v4 batch results nest state under ``extract_result[0]`` — both
        shapes are unwrapped here.
        """
        deadline = time.monotonic() + max(timeout, 1)

        def _remaining() -> float:
            return deadline - time.monotonic()

        while True:
            if _remaining() <= 0:
                raise ServiceError(
                    f"timed out after {int(timeout)}s waiting for extract task",
                    CATEGORY_TIMEOUT,
                )
            raw = self._get_json(status_url, timeout=min(20.0, _remaining()))
            data = self._parse_task_response(raw)

            state = data.get("state")
            if state is None:
                results = data.get("extract_result")
                if isinstance(results, list) and results:
                    state = results[0].get("state")

            if state == "done":
                return data
            if state == "failed":
                err = data.get("err_msg")
                if not err:
                    results = data.get("extract_result")
                    if isinstance(results, list) and results:
                        err = results[0].get("err_msg")
                raise ServiceError(err or "extract task failed", CATEGORY_HTTP)
            if state not in _POLL_STATES:
                raise ServiceError(f"unknown task state '{state}'", CATEGORY_HTTP)
            time.sleep(min(POLL_INTERVAL, max(0.0, _remaining())))

    def _download_text(self, md_url: str, timeout: int) -> str:
        """Download a Markdown text URL (v1 CDN result)."""
        if not md_url:
            raise ServiceError(f"{self.name} task done but no markdown_url", CATEGORY_EMPTY)
        status, _, body = self._http_get(md_url, timeout=min(60, timeout))
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            raise ServiceError(f"{self.name} returned empty content", CATEGORY_EMPTY)
        return text

    def _download_zip_markdown(self, zip_url: str, timeout: int) -> str:
        """Download the v4 result zip and extract the full.md Markdown."""
        if not zip_url:
            raise ServiceError(f"{self.name} task done but no full_zip_url", CATEGORY_EMPTY)
        status, _, body = self._http_get(zip_url, timeout=min(120, timeout))
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                names = [n for n in zf.namelist() if n.endswith("full.md")]
                if not names:
                    raise ServiceError(
                        f"{self.name} result zip has no full.md", CATEGORY_EMPTY
                    )
                # 多文件 zip（罕见）取内容最大的 full.md。
                name = max(names, key=lambda n: zf.getinfo(n).file_size)
                text = zf.read(name).decode("utf-8", errors="replace").strip()
        except zipfile.BadZipFile:
            raise ServiceError(f"{self.name} result zip is corrupt", CATEGORY_EMPTY) from None
        if not text:
            raise ServiceError(f"{self.name} returned empty markdown", CATEGORY_EMPTY)
        return text
