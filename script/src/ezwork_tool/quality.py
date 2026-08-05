"""通用内容质量门（content quality gate）：拦截"假成功"。

背景：回退链原判据 = HTTP 200 + 非空文本。反爬/验证页恰好满足
（如微信公众号的"环境异常"页：HTTP 200 + 182 字符），导致链把垃圾当
成功返回——用户拿到 exit 0 的静默垃圾，比"全部失败"更糟。

通用性原理（不针对任何站点）：拦截页必须告诉用户"你被拦了"，因此
**内容开头（前 HEAD_WINDOW 字符，标题+首段）必然命中通用拦截话术**
（中文：环境异常/完成验证/人机验证/访问过于频繁…；英文：captcha/
just a moment/attention required/verify you are human…）。同时拦截页
几乎没有正文，内容必然短。两条共性组合判定：

- 前缀命中拦截词 + 内容 < BLOCK_LIMIT   → 拦截页（硬失败，继续回退链）
- 前缀命中 + 内容中等（BLOCK_LIMIT~WARN_LIMIT）→ 可疑（返回但标记
  low_quality，链上若有更好结果则替换，全低质时返回 + 警告）
- 前缀命中 + 内容充足（≥ WARN_LIMIT）   → 信任（防误伤"如何绕过
  captcha"类长文——它们标题含词但正文长）
- 前缀未命中                            → 不干预（短新闻/一句话状态
  不受影响——误报只可能发生在"命中拦截词 + 内容短"的组合上）

判定只在 fetch（URL → Markdown）路径生效；本地文件转换不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import CATEGORY_BLOCKED, ServiceError

# 检测窗口：只扫标题 + 开头（拦截页话术必然出现在这里）
HEAD_WINDOW = 200

# 内容长度阈值（字符）
BLOCK_LIMIT = 800    # 低于此 + 命中拦截词 = 拦截页
WARN_LIMIT = 1500    # 低于此 + 命中拦截词 = 可疑（返回但警告）

# 通用拦截/反爬话术（前缀窗口内匹配；全部小写比对，词表本身小写）
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
    """质量判定结果。

    - ok：是否可接受（False = 拦截页，调用方应抛错误继续回退链）
    - low_quality：内容可疑（返回但应警告；链上应继续尝试更好结果）
    - hits：前缀窗口命中的拦截词（log / 错误信息用）
    """

    ok: bool = True
    low_quality: bool = False
    hits: list[str] = field(default_factory=list)


def assess_content(text: str) -> ContentQuality:
    """判定抓取内容是否为拦截页/可疑内容。见模块 docstring。"""
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

    返回 ``(low_quality, reason)``：low_quality=True 表示内容可疑（返回但
    应警告，链上继续尝试更好结果）；reason 为命中的拦截词（log 用）。
    """
    q = assess_content(text)
    if not q.ok:
        raise ServiceError(
            f"{provider} content looks like a bot-check/interstitial page "
            f"(hits: {', '.join(q.hits)}, {len((text or '').strip())} chars)",
            CATEGORY_BLOCKED,
        )
    return q.low_quality, ", ".join(q.hits)
