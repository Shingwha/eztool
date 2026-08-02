---
name: ezwork-tool
description: >-
  Unified CLI for web/image search (Doubao / AnySearch / DeepSeek backends)
  and URL-to-Markdown fetching. Use whenever the user asks to search the web
  (联网搜索 / 豆包 / 火山引擎 / DeepSeek 搜索 / 查最新信息), search images,
  search specialized data sources (academic papers, code, finance quotes,
  security CVEs, legal, travel, news…), or fetch/read the content of a
  webpage or article URL. One command (eztool) covers search AND fetching —
  use it even if the user doesn't name a specific backend.
---

# ezwork-tool (eztool)

一个命令完成「搜索 → 读全文」：`eztool search`（3 个搜索后端）+ `eztool fetch`（URL 转 Markdown）。零依赖、纯标准库，repo 即 skill。

## 什么时候用

| 用户想要 | 用 |
|---|---|
| 联网搜索最新信息（新闻 / 版本发布 / 价格 / 事实核查） | `eztool search "<query>"` |
| 图片搜索 | `eztool search "猫" --image` |
| 学术论文 / 代码 / 金融行情 / CVE / 法律 / 旅行等专业数据 | `eztool search ... --tag <标签>`（先 `eztool tags` 看清单） |
| AI 综合回答 + 来源列表 | `eztool search ... --backend deepseek` |
| 读取网页 / 文章全文 | `eztool fetch <url>` |

## 安装

```bash
# 克隆到 skills 目录后（repo 即 skill）：
cd ezwork-tool && uv tool install .
eztool --version
```

## 配置（第一次使用必读）

所有配置集中在 `~/.config/ezwork-tool/config.json`（`eztool config path` 查看实际路径）。

### 配置命令

| 命令 | 作用 |
|---|---|
| `eztool config set <key> [值]` | 设置配置项（省略值则交互输入，密钥隐藏显示） |
| `eztool config get <key>` | 读取单个配置（密钥脱敏） |
| `eztool config show` | 查看全部配置 |
| `eztool config test [--backend <后端>]` | 用真实请求验证凭证 |
| `eztool config reset <key>` | 恢复默认值 |
| `eztool config clear` | 删除整个配置文件 |
| `eztool config path` | 显示配置文件路径 |

### 各后端最少要配什么

| 后端 | 必须配置 | 不配会怎样 |
|---|---|---|
| `doubao` | `doubao.api_key`，或 `doubao.ak` + `doubao.sk` | 无法使用 doubao 后端（auto 会跳过它） |
| `anysearch` | 无 | 可用（匿名模式，按 IP 限速）；配 `anysearch.api_key` 可提高配额 |
| `deepseek` | `deepseek.api_key` | 无法使用 deepseek 后端 |
| `fetch` | 无 | 可用（firecrawl→markdown.new→jina 全免费） |

### 全部配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `doubao.api_key` | 空 | 豆包 WebSearch API Key（Bearer） |
| `doubao.ak` / `doubao.sk` | 空 | 火山引擎 AK/SK（与 api_key 二选一） |
| `doubao.auth` | 自动 | 鉴权方式 `apikey` / `aksk`，留空自动检测 |
| `doubao.count_web` | 10 | 网页结果数（≤50） |
| `doubao.count_image` | 5 | 图片结果数（≤5） |
| `doubao.need_url` | false | 只返回带落地链接的结果 |
| `doubao.need_content` | false | 只返回带正文的结果 |
| `doubao.content_formats` | 空 | 正文格式 `text` / `markdown` |
| `doubao.time_range` | 空 | 时间范围 `OneDay`/`OneWeek`/`OneMonth`/`OneYear` 或 `YYYY-MM-DD..YYYY-MM-DD` |
| `doubao.industry` | 空 | 行业搜索 `finance` / `game` / `gov` |
| `doubao.timeout` | 30 | 请求超时（秒） |
| `anysearch.api_key` | 空 | 可选；不配则匿名 |
| `anysearch.max_results` | 10 | 结果数（≤20） |
| `deepseek.api_key` | 空 | 必填才可用 deepseek 后端 |
| `deepseek.model` | `deepseek-v4-flash` | 模型 |
| `deepseek.thinking` | `enabled` | 思考模式 `enabled`/`disabled`（enabled 更准但更慢更贵） |
| `deepseek.max_tokens` | 32768 | 最大输出 token 数 |
| `fetch.providers` | `firecrawl,markdown,jina` | 抓取回退链（逗号分隔，按序尝试，首个成功即用） |
| `fetch.timeout` | 30 | 抓取默认超时（秒） |
| `fetch.firecrawl.api_key` | 空 | 可选，提高 firecrawl 限速 |
| `fetch.jina.api_key` | 空 | 可选，提高 jina 限速（无 key 约 20 RPM） |

## 搜索：三个后端能搜什么

| 能力 | doubao | anysearch | deepseek |
|---|---|---|---|
| 通用网页搜索 | ✅ 默认 10 条（≤50） | ✅ 默认 10 条（≤20） | ✅ AI 综合回答 + 来源 |
| 图片搜索 | ✅ `--image` | ❌ | ❌ |
| 域名限定 / 排除 | ✅ `--sites` / `--block-hosts` | ❌ | ❌ |
| 时间范围过滤 | ✅ `--time-range` | ❌ | ❌ |
| 行业搜索（金融/游戏/政务） | ✅ `--industry` | ✅（`--tag finance.*` 等） | ❌ |
| 专业数据源（论文/代码/CVE/航班/法律/新闻…40+） | ❌ | ✅ `--tag` | ❌ |
| 区域 / 语言 | ❌ | ✅ `--zone` / `--language` | ❌ |
| 查询改写 / 权威过滤 | ✅ `--query-rewrite` / `--auth-info-level` | ❌ | ❌ |
| 凭证要求 | 必须 | 可选（匿名可用） | 必须 |

后端选择：`--backend auto`（默认）按已配置凭证路由 **doubao → deepseek → anysearch 兜底**；`--backend doubao|anysearch|deepseek` 强制指定。没有配置任何凭证时 auto 落到 anysearch（匿名），所以**开箱即可搜索**。

## 用法

```bash
# 搜索
eztool search "Rust async 2026"                    # auto 路由，开箱即用
eztool search "AAPL" --tag finance.quote           # [anysearch] 股票行情
eztool search "python" --backend anysearch --zone cn --language zh-CN
eztool search "猫" --image --count 5               # [doubao] 图片搜索
eztool search "Rust" --sites rust-lang.org --time-range OneMonth
eztool search "问题" --backend deepseek            # AI 回答 + 来源列表
eztool search "q" --count 5 --full                 # 公共参数：条数 / 完整正文
eztool search "q" --need-url --need-content        # [doubao] 只要带链接/正文的结果

# 抓取 URL 全文（转干净 Markdown，永不截断）
eztool fetch https://example.com/article
eztool fetch --list-providers

# 数据源标签清单（anysearch 40+ 标签）
eztool tags
```

输出统一为 Markdown：`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + `---` 元数据尾行（backend / total / 耗时 / request_id）。

## Workflow for the Agent

1. **搜索**：直接 `eztool search "<query>"`。默认 auto 路由即可；用户点名豆包/DeepSeek、需要图片或专业数据源时再显式加参数。
2. **读全文**：搜索结果里的 URL 用 `eztool fetch <url>` 抓取（输出永不截断；stderr 的 `[provider] OK (耗时, 字数)` 是回退链日志，可判断走的哪个服务）。
3. **专业搜索**：先 `eztool tags` 看标签清单，再 `--tag` 定向；部分标签还需 `--params '{"key":"value"}'` 补充参数（如 `code.doc` 需要 `library`）。
4. **凭证缺失**：报 `未配置 XX 凭证` 时，引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
5. **失败处理**：exit 1 = 业务失败（无结果 / API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## Notes

- **零依赖**纯标准库；输出无 `--json`，唯一格式就是 Markdown。
- **参数归属**：`--tag/--zone/--language/--params/--anonymous` 仅 anysearch；`--image/--sites/--block-hosts/--time-range/--need-content/--need-url/--content-formats/--industry/--query-rewrite/--auth-info-level/--width-*/--height-*/--shapes` 仅 doubao；`--count/--timeout/--full` 公共。参数传给不支持的后端 → 报错 exit 2，不静默忽略。
- **限流 / 配额**：doubao 5 QPS、免费 500 次/月；anysearch 匿名按 IP 限速；deepseek 每次约 8k–15k token；jina 无 key 约 20 RPM。
- **网络**：jina/firecrawl 不可达时回退链自动跳过（默认超时 10s/60s），全部失败 exit 1。
- **退出码**：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。
