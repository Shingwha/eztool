"""pdfinspector provider — 本地 PDF → Markdown（Firecrawl pdf-inspector，Rust 核心，无 OCR）。

定位：convert 回退链最前。文本型 PDF 本地毫秒级出 Markdown（免费、无网络、
无凭证）；不合适时快速失败，链自动降级到云端（markdown.new → mineru）。

能力边界（无 OCR，靠 pdf_type 智能路由）：
  - ``text_based`` → 本地解析，直接返回
  - ``scanned`` / ``image_based`` / ``mixed`` → 无 OCR 或结果不完整 → 降级云端
  - ``has_encoding_issues`` → 解码不可靠 → 降级云端
  - 非 .pdf / 文件不存在 / 库未安装 → 快速失败（CATEGORY_INVALID / 跳过）

安装：
  - Windows / macOS / glibc-Linux：``pip install pdf-inspector``（官方轮子，
    cp38-abi3，Python ≥3.8）
  - musl（Alpine 等）：PyPI 无 musllinux 轮子，需 gcompat 兼容层 + 手动安装
    manylinux 轮子，详见 SKILL.md。

库本身：https://github.com/firecrawl/pdf-inspector （MIT）
"""
from __future__ import annotations

import os
import time

from ..base import FetchResult, Provider
from ..errors import (
    CATEGORY_EMPTY,
    CATEGORY_INVALID,
    ServiceError,
)
from ..registry import register

# 本地解析只服务 PDF；其它文件类型交给链中云端 provider
SUPPORTED_EXTENSIONS = frozenset({".pdf"})

# 本地无 OCR：这些类型提取不出（或提取不完整）文本
LOCAL_UNSUITABLE = frozenset({"scanned", "image_based", "mixed"})


def _load_library():
    """惰性导入 pdf-inspector；未安装抛可跳过的错误（链继续）。"""
    try:
        import pdf_inspector  # noqa: PLC0415
        return pdf_inspector
    except ImportError:
        raise ServiceError(
            "pdf-inspector 未安装（Windows/macOS/Linux-glibc: "
            "pip install pdf-inspector；musl 环境见 SKILL.md）",
            CATEGORY_INVALID,
        ) from None


@register
class PdfInspectorProvider(Provider):
    """本地 PDF 解析：快、免费、无网络；无 OCR，不合适时快速降级。"""

    name = "pdfinspector"
    categories = frozenset({"convert.file"})

    def has_credentials(self, cfg: dict) -> bool:
        """本地库没有凭证概念：已安装即视为可用。"""
        try:
            _load_library()
            return True
        except ServiceError:
            return False

    def test_credentials(self, cfg: dict) -> str:
        _load_library()  # 未安装抛可跳过的错误
        return "本地库已安装（无需凭证）"

    def convert_file(self, path: str, timeout: int = 60) -> FetchResult:
        """本地 PDF → Markdown；不合适/失败时抛错交给回退链。"""
        t0 = time.monotonic()
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ServiceError(
                f"pdfinspector 仅支持本地 PDF（不支持 {ext or '无扩展名'}），"
                "交给链中其它 provider",
                CATEGORY_INVALID,
            )
        if not os.path.isfile(path):
            raise ServiceError(f"文件不存在: {path}", CATEGORY_INVALID)

        lib = _load_library()
        try:
            result = lib.process_pdf(path)
        except Exception as e:  # Rust 层异常统一包一层，避免崩链
            raise ServiceError(
                f"pdf-inspector 解析失败: {type(e).__name__}: {e}",
                CATEGORY_EMPTY,  # retriable → 链继续走云端
            ) from e

        if result.pdf_type in LOCAL_UNSUITABLE:
            raise ServiceError(
                f"PDF 为 {result.pdf_type}（本地无 OCR 或结果不完整），降级云端",
                CATEGORY_EMPTY,
            )
        if getattr(result, "has_encoding_issues", False):
            raise ServiceError(
                "PDF 存在字体编码问题（has_encoding_issues），降级云端",
                CATEGORY_EMPTY,
            )
        markdown = (result.markdown or "").strip()
        if not markdown:
            raise ServiceError(
                f"PDF 未提取出文本（{result.pdf_type}），降级云端",
                CATEGORY_EMPTY,
            )
        return FetchResult(
            provider=self.name,
            content=markdown,
            url=path,
            elapsed=round(time.monotonic() - t0, 3),
        )
