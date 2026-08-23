"""内容质量门：assess_content 三档判定 + checked_text 抛错（含真实拦截页样本）。"""

import pytest

from eztool.util import (
    BLOCK_LIMIT,
    CATEGORY_BLOCKED,
    WARN_LIMIT,
    ServiceError,
    assess_content,
    checked_text,
)

# 真实样本（2026-08 实测）：微信公众号反爬拦截页
WECHAT_BLOCK_MD_NEW = """Title: Converted Content

URL Source: https://mp.weixin.qq.com/s/sDwK0eFTxcOB_o9E0193vQ

Markdown Content:

## 环境异常

当前环境异常，完成验证后即可继续访问。

去验证
"""

WECHAT_BLOCK_JINA = """Title: Weixin Official Accounts Platform

URL Source: https://mp.weixin.qq.com/s/sDwK0eFTxcOB_o9E0193vQ

Warning: This page maybe requiring CAPTCHA, please make sure you are authorized to access this page.

Markdown Content:
## 环境异常

当前环境异常，完成验证后即可继续访问。
"""


class TestAssessContent:
    def test_wechat_block_page_is_blocked(self):
        q = assess_content(WECHAT_BLOCK_MD_NEW)
        assert not q.ok
        assert "环境异常" in q.hits

    def test_jina_captcha_warning_is_blocked(self):
        # "环境异常" 在 200 字符窗口外；Warning 行的 captcha 已足够判定
        q = assess_content(WECHAT_BLOCK_JINA)
        assert not q.ok
        assert "captcha" in q.hits

    def test_cloudflare_english_page_is_blocked(self):
        q = assess_content("Just a moment...\n\nChecking your browser before accessing.")
        assert not q.ok
        assert "just a moment" in q.hits

    def test_medium_content_with_hit_is_suspicious(self):
        text = "# 环境异常提示\n\n" + ("一些内容。" * 200)
        assert BLOCK_LIMIT <= len(text) < WARN_LIMIT
        q = assess_content(text)
        assert q.ok and q.low_quality
        assert "环境异常" in q.hits

    def test_long_tutorial_mentioning_captcha_is_ok(self):
        # 长文标题含 captcha（技术教程）→ 内容充足，不误伤
        text = "# 如何绕过 Cloudflare captcha 验证\n\n" + ("正文内容。" * 400)
        assert len(text) > WARN_LIMIT
        q = assess_content(text)
        assert q.ok and not q.low_quality

    def test_real_article_and_short_news_are_ok(self):
        article = "# 今天，你可以在秘塔AI上生成视频了！\n\n8月3日，MiniMax 开源了新一代的通用视频模型 H3。" * 20
        assert assess_content(article).ok
        short = "快讯：某公司发布新品。\n\n价格待公布。"  # 短但无拦截词
        q = assess_content(short)
        assert q.ok and not q.low_quality


class TestCheckedText:
    def test_blocked_raises_service_error(self):
        with pytest.raises(ServiceError) as exc:
            checked_text("markdown_new", WECHAT_BLOCK_MD_NEW)
        assert exc.value.category == CATEGORY_BLOCKED

    def test_ok_returns_no_flags(self):
        low, reason = checked_text("tavily", "# 正常文章\n\n正文。")
        assert low is False and reason == ""

    def test_suspicious_returns_low_quality(self):
        text = "# 安全验证\n\n" + ("正文。" * 300)  # ~900 chars ∈ [800, 1500)
        low, reason = checked_text("anysearch", text)
        assert low is True
        assert "安全验证" in reason
