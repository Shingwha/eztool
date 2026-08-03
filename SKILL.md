---
name: ezwork-tool
description: >-
  Unified CLI for search and document conversion: `eztool search web/image/
  paper/data` (Doubao / AnySearch / DeepSeek / OpenAlex / arXiv / Crossref),
  `eztool convert <url|file>` (URL or local file to Markdown), and `eztool
  config`. Use whenever the user asks to search the web (联网搜索 / 豆包 /
  火山引擎 / DeepSeek 搜索 / 查最新信息), search images, search papers /
  literature (论文搜索 / 查文献 / academic), search specialized data sources
  (code, finance quotes, security CVEs, legal, travel, news…), fetch/read the
  content of a webpage or article URL, or convert a local file
  (PDF/DOCX/XLSX/image/CSV…) to Markdown. One command (eztool) covers search
  AND conversion — use it even if the user doesn't name a specific backend.
---

# ezwork-tool (eztool)

一个命令完成「搜索 → 转格式 → 管配置」：**search**（web/image/paper/data/tags）+ **convert**（URL 或本地文件 → Markdown，自动识别输入类型）+ **config**。零依赖纯标准库，repo 即 skill。

## 什么时候用

| 用户想要 | 用 |
|---|---|
| 通用搜索 / AI 综合回答（新闻 / 版本 / 价格 / 事实核查） | `eztool search web "<q>"` |
| 图片搜索（直链 + 尺寸/形状元数据） | `eztool search image "<q>"` |
| 论文 / 文献搜索（年份/作者/引用排序/开放获取） | `eztool search paper "<q>" [--year 2023 --sort cited --oa]` |
| 行情 / 代码 / CVE / 法律 / 旅行等专业数据 | `eztool search data "<q>" --tag <标签>`（先 `eztool search tags` 看清单） |
| 读取网页 / 文章全文 | `eztool convert <url>` |
| 本地文件（PDF/DOCX/XLSX/图片/CSV 等）转 Markdown | `eztool convert <file> [--out out.md]` |
| 配置凭证 / 回退链 | `eztool config`（详见 references/configuration.md） |

## 完整命令参考

> `--count` 默认值已合理（网页/图片/数据 10，论文每源 10），如非必要不要加。

### `eztool search web <query>` — 通用网页搜索
回退链 doubao → anysearch → deepseek（免费/匿名优先，失败自动换下一个）。

| 参数 | 说明 |
|---|---|
| `--providers a,b` | 覆盖回退链（逗号分隔） |
| `--count N` | 结果条数（如非必要不加） |
| `--timeout N` | 请求超时秒数（覆盖配置） |

### `eztool search image <query>` — 图片搜索
仅路由 doubao，返回图片直链 + 尺寸/形状元数据。

| 参数 | 说明 |
|---|---|
| `--width-min/--width-max/--height-min/--height-max N` | 尺寸过滤 |
| `--shapes 横长方形\|竖长方形\|方形` | 图片形状 |
| `--providers a,b` / `--count N` / `--timeout N` | 同上 |

### `eztool search paper <query>` — 论文搜索
openalex + arxiv + crossref 三源**并行**搜索、按 DOI 去重合并（全部免凭证）。

| 参数 | 说明 |
|---|---|
| `--year Y` | 出版年份或区间，如 `2023` 或 `2020-2024` |
| `--author NAME` | 作者名过滤 |
| `--sort relevance\|cited\|date` | 排序（cited/date 在相关性候选集内重排） |
| `--oa` | 仅开放获取论文 |
| `--providers a,b` / `--count N` / `--timeout N` | 同上 |

### `eztool search data <query>` — 专业数据源
anysearch，40+ 标签定向数据源（学术/代码/金融/CVE/法律/旅行…）。

| 参数 | 说明 |
|---|---|
| `--tag TAG` | 数据源标签（`eztool search tags` 查看清单） |
| `--params '{"k":"v"}'` | 标签额外参数（部分标签必填，如 `finance.quote` 需 `{"type":"quote"}`） |
| `--providers a,b` / `--count N` / `--timeout N` | 同上（--count 如非必要不加） |

### `eztool search tags` — 列出全部数据源标签（无参数）

### `eztool convert <url|文件>` — 一切 → Markdown（自动识别输入类型）
- `http(s)://...` → 在线抓取链：markdown_new → jina_reader → firecrawl
- 本地路径 → 本地解析链：pdfinspector → markdown_new → mineru（本地路径不存在报用法错误）

| 参数 | 说明 |
|---|---|
| `--out PATH` | 写入文件而非输出到 stdout |
| `--providers a,b` | 覆盖回退链 |
| `--timeout N` | 超时秒数（覆盖配置） |
| `--list-providers` | 列出两类链的可用 provider |

### `eztool config` — 配置管理
`show`（全部配置 + 文件路径）｜`set <key> [值]`（省略值交互输入）｜`get <key>`｜`reset <key>`｜`test [--providers <名>]`（验证凭证）｜`clear`（删配置文件）。配置存于 `~/.config/ezwork-tool/config.json`，全部键见 `references/configuration.md`。

## 安装

```bash
cd ezwork-tool/script && uv tool install .        # 基础安装
uv tool install ".[local]"                          # 可选：本地 PDF 解析（pdf-inspector）
eztool --version
```

## 核心规则

- **类别路由**：每个搜索子命令对应一个类别（search.web/image/paper/data），provider 声明支持哪些类别，回退链按类别过滤、参数面按类别定制——参数与后端错配从模型上消失（`search image` 只会走 doubao）。
- **回退链**：按类别注册顺序逐个尝试，失败自动换下一个；`--providers a,b` 覆盖链。`search paper` 例外：三源并行 + 去重合并。
- **凭证**：仅 doubao / deepseek 需 api_key；其余匿名可用（mineru 配 Token 自动升级 v4）。缺凭证报错时引导用户 `eztool config set`，**绝不硬编码密钥**。
- **输出**：统一 Markdown；stderr 的 `[provider] OK (耗时, 结果数)` 是回退链日志（可判断实际走的服务）。退出码：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。

## Workflow for the Agent

1. **搜索**：直接 `eztool search web "<query>"`；要图片/论文/专业数据用对应子命令。
2. **论文**：`eztool search paper "<query>"`。`--sort cited` 如非必要少用（多词查询候选集内可能混入泛相关高引综述，默认相关性排序最稳）；中文文献无公开 API，用 `search data --tag academic.search` 兜底。
3. **读全文**：搜索结果里的 URL 用 `eztool convert <url>` 抓取（输出永不截断）；本地文件用 `eztool convert <file> --out out.md`。markdown.new 不支持的格式（PPTX/GIF/BMP 等）自动回退 MinerU。
4. **专业搜索**：先 `eztool search tags` 看清单，再 `eztool search data "<q>" --tag <标签>`。
5. **凭证缺失**：报 `未配置 XX 凭证` 时引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
6. **失败处理**：exit 1 = 业务失败（无结果/API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## 资源导航（按需读取）

| 需要 | 读 |
|---|---|
| 配置项大全 / 配置命令 / 限流配额 | `references/configuration.md` |
| 11 个 provider 类别声明总表 / 参数归属 / paper 细节 / 输出格式 | `references/backends.md` |
| 安装 / 测试 / 更新 / 架构（开发者） | `references/development.md` |
| 代码（repo 即 skill 的实现） | `script/`（`cd script && uv tool install ".[local]"` 安装） |
