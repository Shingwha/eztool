"""Provider implementations. **唯一的注册点**：import 即注册（@register）。

新增 provider：写一个模块放这里（类声明 name/categories/config/params/
priority/auth_required），然后在下面 import 列表加一行——config 键、CLI 参数、
默认链、config show 全部自动出现。
"""
from __future__ import annotations

from . import (  # noqa: F401  (side-effect: register)
    anydoc,
    anysearch,
    deepseek,
    doubao,
    exa,
    firecrawl,
    jina_reader,
    markdown_new,
    mineru,
    tavily,
)
