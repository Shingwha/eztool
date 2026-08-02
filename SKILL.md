---
name: ezwork-tool
description: >-
  Unified CLI for web/image search (Doubao / AnySearch / DeepSeek backends)
  and URL-to-Markdown fetching. Use whenever the user asks to search the web
  (联网搜索 / 豆包 / 火山引擎 / DeepSeek 搜索), search images, search
  specialized data sources (academic, code, finance, security, legal, travel…),
  or fetch/read the content of a webpage URL. One tool, one skill — replaces
  doubao-websearch / anysearch / deepseek-ws / ezwork-fetch.
---

# ezwork-tool (eztool)

统一 CLI：**搜索**（3 个后端）+ **URL 抓取转 Markdown**。零依赖、纯标准库，一个命令搞定"搜到 → 读到"。

```
搜索后端：doubao（豆包/火山 WebSearch，web+图片，结构化结果）
         anysearch（AnySearch，40+ 数据源标签，匿名可用）
         deepseek（DeepSeek 服务端搜索，AI 合成回答 + 来源）
抓取：    firecrawl → markdown.new → jina 三级回退链
```

## 安装

```bash
cd ezwork-tool && uv tool install .
eztool --help
```

## 配置（一次性）

```bash
eztool config import-legacy        # 从旧工具（doubao-websearch 等）导入已有凭证
eztool config set doubao.api_key   # 或手动设置（交互输入，隐藏显示）
eztool config set deepseek.api_key # DeepSeek key（https://platform.deepseek.com）
eztool config show                 # 查看（secret 脱敏）
eztool config test                 # 验证所有已配置后端的凭证
```

配置优先级：CLI 参数 > 配置文件（`~/.config/ezwork-tool/config.json`，`eztool config path` 查看）。

## 用法

```bash
# 搜索（--backend 默认 auto：按已配置凭证路由 doubao → deepseek → anysearch 兜底）
eztool search "Rust async 2026"
eztool search "AAPL" --tag finance.quote      # [anysearch] 数据源
eztool search "Python 3.14" --zone cn --language zh-CN
eztool search "猫" --image --width-min 800    # [doubao] 图片搜索
eztool search "X" --sites github.com --time-range OneMonth  # [doubao]
eztool search "历史问题" --backend deepseek    # AI 合成回答
eztool search "q" --count 5 --full            # 公共参数：条数 / 完整正文

# 抓取 URL → Markdown
eztool fetch https://example.com/article
eztool fetch --list-providers

# 数据源标签目录（anysearch）
eztool tags
```

## Workflow for the Agent

1. **搜索**：`eztool search "<query>"`。默认 auto 路由，无需指定后端；用户点名豆包/DeepSeek 或需要图片/数据源时再显式指定。
2. **读网页**：搜索结果里有 URL 时，用 `eztool fetch <url>` 抓全文（输出永不截断；stderr 的 `[provider] OK (耗时, 字数)` 是回退链日志）。
3. **结构化数据源**：先 `eztool tags` 看标签，再 `--tag <name>` 定向搜索。
4. **凭证问题**：报 `未配置 XX 凭证` 时，引导用户 `eztool config set <key>` 或 `eztool config import-legacy`，绝不硬编码密钥。
5. **失败处理**：exit 1 = 业务失败（无结果/API 错误），exit 2 = 用法或凭证问题。错误在 stderr，形式 `error: <原因>` + `code: <语义码>`。

## Notes

- **输出统一**：三个后端返回同一 markdown 结构——`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + 元数据尾行。无 `--json`。
- **auto 路由**：doubao 有凭证（api_key 或 ak+sk）→ doubao；否则 deepseek 有 key → deepseek；否则 anysearch（匿名，无需任何凭证）。
- **参数归属**：`--tag/--zone/--language/--params/--anonymous` 仅 anysearch；`--image/--sites/--block-hosts/--time-range/--need-content/--need-url/--content-formats/--industry/--query-rewrite/--auth-info-level/--width-*/--height-*/--shapes` 仅 doubao；`--count/--timeout/--full` 公共。传错后端报错 exit 2。
- **deepseek 思考模式**：config 控制 `eztool config set deepseek.thinking disabled`（enabled 更准但更慢更贵）。
- **限流/配额**：doubao 默认 5 QPS、免费 500 次/月；anysearch 匿名按 IP 限速；deepseek 每次约 8k–15k token。jina 无 key ~20 RPM。
- **网络**：某些网络下 jina/firecrawl 不可达时回退链自动跳过（默认超时 10s/60s）。
- **退出码**：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。
