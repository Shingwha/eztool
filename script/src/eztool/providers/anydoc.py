"""anydoc provider — 本地文档 → Markdown（Firecrawl anydoc，Rust 核心）。

定位：file 链最前。本地毫秒级出 Markdown（免费、无网络、无凭证）；
不合适时快速失败，链自动降级到云端（markdown_new → mineru）。

能力分层（按扩展名路由）：
  - anydoc 引擎（14 格式族）：doc/docx/docm/ppt/pps/pot/pptx/pptm/ppsx/ppsm/
    xls/xlsx/xlsm/xlsb/odt/ods/odp/rtf/epub/csv/pdf —— 扫描版 PDF 等无文本
    文档报错 → 降级云端（MinerU 提供 OCR）
  - 纯文本直读：txt/md/markdown/log —— 原样输出（utf-8/gbk 自动探测）
  - 结构化文本：json/yaml/yml/xml —— 代码块包裹，保持结构不丢失
  - HTML：html/htm —— 标准库轻量转 Markdown（标题/段落/表格/列表/链接/代码）

安装：``pip install firecrawl-anydoc``（Windows / macOS / glibc / musl 均有官方轮子）。
库：https://github.com/firecrawl/anydoc （MIT；PDF 支持内嵌 pdf-inspector 引擎）
"""
from __future__ import annotations

import os
import time
from html.parser import HTMLParser

from ..provider import FetchResult, Provider
from ..util import (
    CATEGORY_EMPTY,
    CATEGORY_INVALID,
    ServiceError,
)
from ..provider import register

# ── anydoc 引擎格式（Rust）───────────────────────────────────────────────
ENGINE_EXTENSIONS = frozenset({
    ".doc", ".docx", ".docm",                # Word
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm",  # PowerPoint
    ".xls", ".xlsx", ".xlsm", ".xlsb",       # Excel
    ".odt", ".ods", ".odp",                  # OpenDocument
    ".rtf", ".epub", ".csv", ".pdf",
})
# ── 纯文本直读（原样输出）───────────────────────────────────────────────
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".log"})
# ── 结构化文本（代码块包裹，保持结构）──────────────────────────────────
CODE_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".xml"})
# ── HTML（轻量本地转换）────────────────────────────────────────────────
HTML_EXTENSIONS = frozenset({".html", ".htm"})

SUPPORTED_EXTENSIONS = (
    ENGINE_EXTENSIONS | TEXT_EXTENSIONS | CODE_EXTENSIONS | HTML_EXTENSIONS
)


def _load_library():
    """惰性导入 anydoc；未安装抛可跳过的错误（链继续）。"""
    try:
        import anydoc  # noqa: PLC0415
        return anydoc
    except ImportError:
        raise ServiceError(
            "firecrawl-anydoc is not installed (Windows/macOS/Linux: pip install "
            "firecrawl-anydoc, or install eztool with the [local] extra)",
            CATEGORY_INVALID,
        ) from None


def _read_text(path: str) -> str:
    """读取文本文件，utf-8 → gbk → latin-1 逐级探测（兼容中文环境 GBK 文件）。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ── HTML → Markdown 转换器（标准库 html.parser，零依赖）─────────────────
_SKIP_TAGS = frozenset({
    "script", "style", "nav", "noscript", "iframe", "template", "svg",
    "form", "select", "button", "input", "textarea", "head", "meta",
    "link", "title", "footer", "header", "aside", "figure", "figcaption",
})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LIST_TAGS = frozenset({"ul", "ol"})
_TABLE_TAGS = frozenset({"table", "tr", "td", "th"})
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "blockquote", "hr", "pre", "br",
}) | _HEADING_TAGS | _LIST_TAGS | _TABLE_TAGS | {"li"}
_INLINE_TAGS = frozenset({
    "a", "strong", "b", "em", "i", "u", "s", "strike", "del", "ins",
    "code", "kbd", "q", "mark", "span", "small", "sup", "sub", "label",
})


class _HtmlToMarkdown(HTMLParser):
    """流式 HTML → Markdown：行缓冲 + inline 栈（嵌套链接/样式正确包裹）。

    块级元素 flush 成行；inline 元素在栈内包裹（a→[t](u)、strong→**t**、
    code→`t`、img→![alt](src)）；script/style/nav 等噪声直接丢弃。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._stack: list[list] = []        # inline 栈：[text, kind, href/marker]
        self._list_stack: list[bool] = []   # True=ol
        self._table_rows: list[list[str]] | None = None
        self._row: list[str] = []
        self._in_cell = False
        self._skip = 0                      # script/style 嵌套深度
        self._in_pre = False
        self._pre_buf: list[str] = []

    # ── inline 栈 ──
    def _push(self, kind: str, href: str | None = None):
        self._stack.append(["", kind, href])

    def _pop(self, kind: str, backfill: bool = True) -> str:
        """弹出栈顶 inline 并包裹；默认把结果回填到父级（嵌套包裹正确）。"""
        if not self._stack:
            return ""
        text, k, href = self._stack.pop()
        if kind != k:
            wrapped = text
        elif kind == "a":
            wrapped = f"[{text.strip()}]({href})" if href else text.strip()
        elif kind == "li":
            wrapped = f"{href or '-'} {text.strip()}".rstrip()
        elif kind in ("strong", "b"):
            wrapped = f"**{text.strip()}**" if text.strip() else ""
        elif kind in ("em", "i"):
            wrapped = f"*{text.strip()}*" if text.strip() else ""
        elif kind in ("s", "strike", "del"):
            wrapped = f"~~{text.strip()}~~" if text.strip() else ""
        elif kind == "code":
            wrapped = f"`{text.strip()}`"
        elif kind == "q":
            wrapped = f'"{text.strip()}"'
        else:
            wrapped = text
        if backfill and self._stack:
            self._stack[-1][0] += wrapped
        return wrapped

    def _flush(self) -> str:
        parts = []
        while self._stack:
            parts.append(self._pop(self._stack[-1][1]))
        return "".join(reversed(parts))

    def _data(self) -> str:
        return self._stack[-1][0] if self._stack else ""

    # ── 行输出 ──
    def _emit(self, line: str):
        line = line.strip()
        if line:
            self.lines.append(line)

    def _emit_para(self, text: str):
        text = " ".join(text.split())
        if text:
            self.lines.append(text)

    # ── 事件 ──
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in _SKIP_TAGS:
            if tag in ("script", "style", "template"):
                self._skip += 1
            return
        if self._skip:
            return
        if tag in _HEADING_TAGS:
            self._push("h", tag)          # href 槽暂存级别
        elif tag == "p":
            self._push("p")
        elif tag == "blockquote":
            self._push("quote")
        elif tag == "a":
            self._push("a", attrs.get("href"))
        elif tag == "img":
            alt = attrs.get("alt", "")
            src = attrs.get("src", "")
            self._emit(f"![{alt}]({src})" if src else alt or "[image]")
        elif tag in ("strong", "b", "em", "i", "u", "s", "strike", "del",
                     "ins", "code", "kbd", "q", "mark"):
            self._push(tag)
        elif tag == "br":
            if self._stack:
                self._stack[-1][0] += "\n"
        elif tag == "hr":
            self.lines.append("---")
        elif tag in _LIST_TAGS:
            self._list_stack.append(tag == "ol")
        elif tag == "li":
            marker = f"{len(self._list_stack)}." if self._list_stack and self._list_stack[-1] else "-"
            self._push("li", marker)
        elif tag == "table":
            self._table_rows = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._push("cell")
        elif tag == "pre":
            self._in_pre = True
            self._pre_buf = []
        elif tag in ("div", "section", "article", "span"):
            self._push("span")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "template"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _HEADING_TAGS:
            text = self._pop("h")
            self._emit("#" * int(tag[1]) + " " + text.strip())
        elif tag == "p":
            text = self._pop("p", backfill=False)
            if self._in_cell and self._stack and self._stack[-1][1] == "cell":
                self._stack[-1][0] += (" " + text.strip())
            else:
                self._emit_para(text)
        elif tag == "blockquote":
            text = self._pop("quote")
            self._emit("> " + text.strip())
        elif tag == "a":
            self._pop("a")
        elif tag in ("strong", "b", "em", "i", "u", "s", "strike", "del",
                     "ins", "code", "kbd", "q", "mark"):
            self._pop(tag)
        elif tag == "li":
            text = self._pop("li", backfill=False)
            depth = max(0, len(self._list_stack) - 1)
            self._emit("  " * depth + text)
        elif tag in _LIST_TAGS:
            if self._list_stack:
                self._list_stack.pop()
        elif tag in ("td", "th"):
            text = self._pop("cell", backfill=False)
            self._in_cell = False
            self._row.append(" ".join(text.split()))
        elif tag == "tr":
            if self._row and self._table_rows is not None:
                self._table_rows.append(self._row)
            self._row = []
        elif tag == "table":
            if self._table_rows:
                width = max(len(r) for r in self._table_rows)
                header = self._table_rows[0]
                self._emit("| " + " | ".join(header) + " |")
                self._emit("| " + " | ".join(["---"] * width) + " |")
                for r in self._table_rows[1:]:
                    self._emit("| " + " | ".join(r + [""] * (width - len(r))) + " |")
            self._table_rows = None
        elif tag == "pre":
            self._in_pre = False
            code = "\n".join(self._pre_buf).strip("\n")
            if code:
                self.lines.append("```")
                self.lines.append(code)
                self.lines.append("```")
        elif tag in ("div", "section", "article", "span"):
            self._pop("span")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_pre:
            self._pre_buf.append(data)
            return
        if self._stack:
            self._stack[-1][0] += data

    def render(self) -> str:
        if self._stack:  # 收尾未闭合标签
            self._flush()
        lines, prev, in_code = [], None, False
        for line in self.lines:
            if prev is not None:
                if in_code or (line.startswith("|") and prev.startswith("|")):
                    lines.append("\n")
                else:
                    lines.append("\n\n")
            lines.append(line)
            if line.strip() == "```":
                in_code = not in_code
            prev = line
        return "".join(lines).strip() + "\n"


def _html_to_markdown(text: str) -> str:
    """轻量 HTML → Markdown（标准库 html.parser，零依赖）。

    覆盖标题/段落/表格/列表/链接/图片/代码块/行内样式；script/style/nav
    等噪声标签直接丢弃。复杂页面质量有限，可手动 --providers 换云端。
    """
    parser = _HtmlToMarkdown()
    parser.feed(text)
    parser.close()
    return parser.render()


@register
class AnydocProvider(Provider):
    """本地文档解析：anydoc 引擎（14 格式）+ 纯文本/结构化/HTML 直读。"""

    name = "anydoc"
    categories = frozenset({"file"})
    # 本地库无需凭证
    config = {
        "timeout": {"default": 60, "hint": "anydoc local parsing timeout in seconds"},
    }
    priority = {"file": 10}

    def has_credentials(self) -> bool:
        """本地库没有凭证概念：已安装即视为可用（纯文本类不需要库）。"""
        try:
            _load_library()
            return True
        except ServiceError:
            return False

    def test_credentials(self) -> str:
        _load_library()  # 未安装抛可跳过的错误
        return "local library installed (no credentials needed)"

    def convert_file(self, path: str, timeout: int = 60) -> FetchResult:
        """本地文档 → Markdown；不合适/失败时抛错交给回退链。"""
        t0 = time.monotonic()
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ServiceError(
                f"anydoc does not support {ext or 'no extension'}; "
                "leaving it to the rest of the chain",
                CATEGORY_INVALID,
            )
        if not os.path.isfile(path):
            raise ServiceError(f"file not found: {path}", CATEGORY_INVALID)

        # 纯文本 / 结构化 / HTML：标准库直读，无需 anydoc 库
        if ext in TEXT_EXTENSIONS:
            content = _read_text(path)
        elif ext in CODE_EXTENSIONS:
            content = "```" + ext.lstrip(".") + "\n" + _read_text(path).rstrip() + "\n```"
        elif ext in HTML_EXTENSIONS:
            content = _html_to_markdown(_read_text(path))
        else:
            lib = _load_library()
            try:
                content = lib.to_markdown(path)
            except OSError as e:
                raise ServiceError(
                    f"failed to read file: {e}", CATEGORY_INVALID,
                ) from e
            except Exception as e:  # ConvertError（扫描版 PDF / 加密 / 损坏）等
                raise ServiceError(
                    f"anydoc failed to parse: {type(e).__name__}: {e}",
                    CATEGORY_EMPTY,  # retriable → 链继续走云端（MinerU 有 OCR）
                ) from e

        content = (content or "").strip()
        if not content:
            raise ServiceError(
                "no text extracted from the document; falling back to the cloud",
                CATEGORY_EMPTY,
            )
        return FetchResult(
            provider=self.name,
            content=content,
            url=path,
            elapsed=round(time.monotonic() - t0, 3),
        )
