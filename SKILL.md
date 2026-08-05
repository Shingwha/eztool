---
name: ezwork-tool
description: >-
  Unified CLI for search and document conversion: `eztool search web/image/
  data` (Doubao / AnySearch / DeepSeek / Tavily / Exa), `eztool
  convert <url|file>` (URL or local file to Markdown), and `eztool
  config`. Use whenever the user asks to search the web (联网搜索 / 豆包 /
  火山引擎 / DeepSeek 搜索 / 查最新信息), search images, search specialized data sources
  (code, finance quotes, security CVEs, legal, travel, news…), fetch/read the
  content of a webpage or article URL, or convert a local file
  (PDF/DOCX/XLSX/image/CSV…) to Markdown. One command (eztool) covers search
  AND conversion — use it even if the user doesn't name a specific backend.
---

# ezwork-tool (eztool)

一个命令完成「搜索 → 转格式 → 管配置」：**search**（web/image/data/tags）+ **convert**（URL 或本地文件 → Markdown，自动识别输入类型）+ **config**。零依赖纯标准库，repo 即 skill。

## 什么时候用

| 用户想要 | 用 |
|---|---|
| 通用搜索 / AI 综合回答（新闻 / 版本 / 价格 / 事实核查） | `eztool search web "<q>"` |
| 图片搜索（直链 + 尺寸/形状元数据） | `eztool search image "<q>"` |
| 行情 / 代码 / CVE / 法律 / 旅行等专业数据 | `eztool search data "<q>" --tag <标签>`（先 `eztool search tags` 看清单） |
| 读取网页 / 文章全文 | `eztool convert <url>` |
| 本地文件（PDF/DOCX/XLSX/图片/CSV 等）转 Markdown | `eztool convert <file> [--out out.md]` |
| 配置凭证 / 回退链 | `eztool config`（详见 references/guide.md） |

## 完整命令参考

> `--count` 默认值已合理（网页/数据 20，图片 5），如非必要不要加。

### `eztool search web <query>` — 通用网页搜索
回退链 doubao → anysearch → deepseek（免费/匿名优先，失败自动换下一个）。

| 参数 | 说明 |
|---|---|
| `--providers a,b` | **并行**跑多个后端（逗号分隔，结果合并去重标注来源）；1 个 = 单跑 |
| `--count N` | **每个 provider** 的结果条数（如非必要不加） |
| `--timeout N` | 请求超时秒数（覆盖配置） |

### `eztool search image <query>` — 图片搜索
仅路由 doubao，返回图片直链 + 尺寸/形状元数据。

| 参数 | 说明 |
|---|---|
| `--width-min/--width-max/--height-min/--height-max N` | 尺寸过滤 |
| `--shapes 横长方形\|竖长方形\|方形` | 图片形状 |
| `--providers a,b` / `--count N` / `--timeout N` | 同上（并行合并时来源标注 `[doubao]` 等） |

### `eztool search data <query>` — 专业数据源
anysearch，40+ 标签定向数据源（学术/代码/金融/CVE/法律/旅行…）。

| 参数 | 说明 |
|---|---|
| `--tag TAG` | 数据源标签（`eztool search tags` 查看清单） |
| `--params '{"k":"v"}'` | 标签额外参数（部分标签必填，如 `finance.quote` 需 `{"type":"quote"}`） |
| `--providers a,b` / `--count N` / `--timeout N` | 同上（--count 如非必要不加） |

### `eztool search tags` — 列出全部数据源标签（无参数）

### `eztool convert <url|文件>` — 一切 → Markdown（自动识别输入类型）
- `http(s)://...` → 在线抓取链：markdown_new → jina_reader → anysearch → tavily → firecrawl（结果过质量门，反爬验证页自动拦截回退，tavily 兜底）
- 本地路径 → 本地解析链：anydoc → markdown_new → mineru（本地路径不存在报用法错误）

| 参数 | 说明 |
|---|---|
| `--out PATH` | 写入文件而非输出到 stdout |
| `--providers a,b` | 并行跑指定后端（取先成功者）；1 个 = 单跑（如微信文章 `--providers tavily`） |
| `--timeout N` | 超时秒数（覆盖配置） |
| `--list-providers` | 列出两类链的可用 provider |

> 微信公众号文章自动处理：默认链的抓取结果会过**质量门**（反爬/验证页判定），微信验证页被拦截后自动回退到 tavily 拿到全文，无需手动指定 provider。

### `eztool config` — 配置管理
`show`（全部配置 + 文件路径）｜`set <key> [值]`（省略值交互输入）｜`get <key>`｜`reset <key>`｜`test [--providers <名>]`（验证凭证）｜`clear`（删配置文件）。配置存于 `~/.config/ezwork-tool/config.json`，全部键见 `references/guide.md`。

## 安装

```bash
cd ezwork-tool/script && uv tool install .        # 基础安装
uv tool install ".[local]"                          # 可选：本地文档解析（firecrawl-anydoc，14 格式）
eztool --version
```

## 核心规则

- **类别路由**：每个搜索子命令对应一个类别（search.web/image/data），provider 声明支持哪些类别，回退链按类别过滤、参数面按类别定制——参数与后端错配从模型上消失（`search image` 只会走 doubao）。
- **回退链（只存在于 config）**：默认链 = 类别内 provider 按声明的 `priority` 排序自动派生（`config set search.web.providers "a,b"` 可显式覆盖）；**必须配凭证的 provider（doubao/deepseek/exa）未配时自动跳过**，匿名可用的（anysearch/tavily/firecrawl/jina/markdown_new/mineru/anydoc）永远进链；失败自动换下一个。命令行 `--providers` 不再覆盖链，而是**并行**。
- **质量门**：URL 抓取结果过通用内容质量门——反爬/验证页（"环境异常"/captcha/Cloudflare 等拦截话术出现在内容开头且内容短）判为拦截页，继续回退链；结果可疑但可能真实（命中话术但内容中等长度）时返回并警告；无拦截话术的短内容不受影响。微信验证页等"假成功"（HTTP 200 + 非空但实为拦截页）从此不再静默返回。
- **凭证**：doubao / deepseek / exa 必须配 api_key（未配时默认链跳过、`--providers` 显式点名则报错）；其余匿名可用（mineru 配 Token 自动升级 v4）。缺凭证报错时引导用户 `eztool config set`，**绝不硬编码密钥**。
- **输出**：统一 Markdown；stderr 的 `[provider] OK (耗时, 结果数)` 是回退链日志（可判断实际走的服务）。退出码：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。

## Workflow for the Agent

1. **搜索**：直接 `eztool search web "<query>"`；要图片/专业数据用对应子命令。
2. **读全文**：搜索结果里的 URL 用 `eztool convert <url>` 抓取（输出永不截断）；本地文件用 `eztool convert <file> --out out.md`。markdown.new 不支持的格式（PPTX/GIF/BMP 等）自动回退 MinerU。
3. **专业搜索**：先 `eztool search tags` 看清单，再 `eztool search data "<q>" --tag <标签>`。中文文献等无公开 API 的领域用 `search data --tag academic.search` 兜底。
4. **凭证缺失**：报 `未配置 XX 凭证` 时引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
5. **失败处理**：exit 1 = 业务失败（无结果/API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## 资源导航（按需读取）

| 需要 | 读 |
|---|---|
| 配置项 / provider 表 / 架构 / 安装测试（开发者） | `references/guide.md` |
| 代码（repo 即 skill 的实现） | `script/`（`cd script && uv tool install ".[local]"` 安装） |
