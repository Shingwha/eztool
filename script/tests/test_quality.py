"""通用内容质量门（quality gate）测试：拦截"假成功"（反爬/验证页）。"""

import unittest

from ezwork_tool import provider as pmod
from ezwork_tool.util import (
    BLOCK_LIMIT,
    WARN_LIMIT,
    assess_content,
    checked_text,
)

# 真实样本（2026-08 实测）
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


class TestAssessContent(unittest.TestCase):
    def test_wechat_block_page_is_blocked(self):
        q = assess_content(WECHAT_BLOCK_MD_NEW)
        self.assertFalse(q.ok)
        self.assertIn("环境异常", q.hits)

    def test_jina_captcha_warning_is_blocked(self):
        q = assess_content(WECHAT_BLOCK_JINA)
        self.assertFalse(q.ok)
        # "环境异常" 在 200 字符窗口外；Warning 行的 captcha 已足够判定
        self.assertIn("captcha", q.hits)

    def test_real_article_is_ok(self):
        # tavily 抓到的真全文（无拦截话术）→ 完全放行
        text = "# 今天，你可以在秘塔AI上生成视频了！\n\n8月3日，MiniMax 开源了新一代的通用视频模型 H3。" * 20
        q = assess_content(text)
        self.assertTrue(q.ok)
        self.assertFalse(q.low_quality)

    def test_short_news_without_hits_is_ok(self):
        # 短内容但无拦截话术 → 不误伤
        text = "快讯：某公司发布新品。\n\n价格待公布。"
        q = assess_content(text)
        self.assertTrue(q.ok)
        self.assertFalse(q.low_quality)

    def test_long_tutorial_mentioning_captcha_is_ok(self):
        # 长文标题含 captcha（技术教程）→ 内容充足，信任
        text = "# 如何绕过 Cloudflare captcha 验证\n\n" + ("正文内容。" * 400)
        self.assertGreater(len(text), WARN_LIMIT)
        q = assess_content(text)
        self.assertTrue(q.ok)
        self.assertFalse(q.low_quality)

    def test_medium_content_with_hit_is_suspicious(self):
        # 命中拦截词 + 中等长度 → 可疑（low_quality，不失败）
        text = "# 环境异常提示\n\n" + ("一些内容。" * 200)
        self.assertGreaterEqual(len(text), BLOCK_LIMIT)
        self.assertLess(len(text), WARN_LIMIT)
        q = assess_content(text)
        self.assertTrue(q.ok)
        self.assertTrue(q.low_quality)
        self.assertIn("环境异常", q.hits)

    def test_cloudflare_english_page_is_blocked(self):
        text = "Just a moment...\n\nChecking your browser before accessing."
        q = assess_content(text)
        self.assertFalse(q.ok)
        self.assertIn("just a moment", q.hits)


class TestCheckedText(unittest.TestCase):
    def test_blocked_raises_service_error(self):
        from ezwork_tool.util import CATEGORY_BLOCKED

        with self.assertRaises(pmod.ServiceError) as ctx:
            checked_text("markdown_new", WECHAT_BLOCK_MD_NEW)
        self.assertEqual(ctx.exception.category, CATEGORY_BLOCKED)

    def test_ok_returns_flags(self):
        low, reason = checked_text("tavily", "# 正常文章\n\n正文。")
        self.assertFalse(low)
        self.assertEqual(reason, "")

    def test_suspicious_returns_low_quality(self):
        text = "# 安全验证\n\n" + ("正文。" * 300)  # ~900 chars ∈ [800, 1500)
        low, reason = checked_text("anysearch", text)
        self.assertTrue(low)
        self.assertIn("安全验证", reason)


if __name__ == "__main__":
    unittest.main()
