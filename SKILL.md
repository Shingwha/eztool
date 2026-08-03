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

一个命令完成「搜索 → 转格式 → 管配置」：**search**（web / image / paper / data / tags 子命令）
+ **convert**（URL 或本地文件 → Markdown，自动识别输入类型）+ **config**。
零依赖纯标准库，repo 即 skill。

## 什么时候用

| 用户想要 | 用 |
|---|---|
| 通用搜索 / AI 综合回答（新闻 / 版本发布 / 价格 / 事实核查） | `eztool search web "<q>"` |
| 图片搜索（直链 + 尺寸/形状元数据） | `eztool search image "<q>"` |
| **论文 / 文献搜索**（年份/作者/引用排序/开放获取过滤） | `eztool search paper "<q>" [--year 2023 --sort cited --oa]` |
| 行情 / 代码 / CVE / 法律 / 旅行等专业数据 | `eztool search data "<q>" --tag <标签>`（先 `eztool search tags` 看清单） |
| 查看数据源标签 | `eztool search tags` |
| 读取网页 / 文章全文 | `eztool convert <url>` |
| 本地文件（PDF/DOCX/XLSX/图片/CSV 等）转 Markdown | `eztool convert <file> [--out out.md]` |
| 配置凭证 / 回退链 | `eztool config` |

## 安装

```bash
cd ezwork-tool/script && uv tool install .           # 基础安装
uv tool install ".[local]"                            # 可选：本地 PDF 解析（pdf-inspector）
eztool --version
```

## 命令速查

```bash
eztool search web "Rust async 2026"               # 通用搜索：doubao→anysearch→deepseek 回退链
eztool search image "猫" --width-min 800           # 图片搜索（只路由 doubao，无 --image 参数）
eztool search paper "vision transformer"          # 论文搜索：openalex+arxiv+crossref 三源并行汇总
eztool search paper "LLM reasoning" --year 2024 --sort cited --count 20
eztool search data "AAPL" --tag finance.quote     # 专业数据源（anysearch）
eztool search tags                                # 数据源标签清单（40+）
eztool convert https://example.com/article        # URL 全文 → 干净 Markdown
eztool convert report.pdf --out report.md         # 本地文件 → Markdown
eztool config set providers.doubao.api_key        # 配置凭证（省略值交互输入）
eztool config test                                # 验证已配置的凭证
```

## 核心规则

- **类别路由**：每个搜索子命令对应一个类别（`search.web` / `search.image` / `search.paper` / `search.data`），provider 声明自己支持哪些类别，回退链按类别过滤、参数面按类别定制——参数与后端错配从模型上消失（`search image` 只会走支持图片的 doubao）。
- **回退链**：默认按类别注册顺序逐个尝试，失败自动换下一个（免费/匿名可用优先）；`--providers a,b` 可覆盖链（逗号分隔）。`search paper` 例外：三源**并行**搜索 + 去重合并。
- **convert 自动识别**：`http(s)://` → 在线抓取链（markdown_new → jina_reader → firecrawl）；本地路径 → 本地解析链（pdfinspector → markdown_new → mineru）。本地路径不存在会报用法错误。
- **凭证**：只有 doubao / deepseek 需要 api_key；anysearch / openalex / arxiv / crossref / markdown_new / jina_reader / firecrawl 匿名可用；mineru 配 Token 自动升级 v4（不配走 v1 轻量）。缺凭证报错时引导用户 `eztool config set`，**绝不硬编码密钥**。
- **输出**：统一 Markdown；stderr 的 `[provider] OK (耗时, 结果数)` 是回退链日志（可判断实际走的服务）。
- **退出码**：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。

## Workflow for the Agent

1. **搜索**：直接 `eztool search web "<query>"`。默认回退链即可；用户点名豆包/DeepSeek、要图片或专业数据时用对应子命令。
2. **论文 / 文献**：`eztool search paper "<query>"`（三源并行，免凭证）。`--year` 限年份、`--oa` 找开放获取；多源合并自动按 DOI 去重。
   - **`--sort cited` 如非必要少用**：它先按相关性取候选再按引用重排，多词查询（≥3 个词）时候选内仍可能混入只命中个别词的"泛相关"高引综述。**默认相关性排序最稳**；只有单关键词/短语或用户明确要"高引经典论文"时才用。
3. **读全文**：搜索结果里的 URL 用 `eztool convert <url>` 抓取（输出永不截断）。本地文件用 `eztool convert <file>` 转 Markdown，`--out` 写文件；不支持的格式/超限会在本地快速报错。markdown.new 不支持的格式（如 PPTX、GIF/BMP）自动回退 MinerU（免费无 Token，异步提取；配置 `providers.mineru.api_key` 后升级 v4：≤200MB/200 页/支持 doc/ppt/xls/html）。
4. **专业搜索**：先 `eztool search tags` 看标签清单，再 `eztool search data "<q>" --tag <标签>`。
5. **凭证缺失**：报 `未配置 XX 凭证` 时，引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
6. **失败处理**：exit 1 = 业务失败（无结果 / API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## Provider 扩展指南（声明式，公共代码零改动）

- **新增 provider**：在 `script/src/ezwork_tool/providers/` 建模块，子类化 `Provider`，声明 `name` + `categories`（如 `{"search.web"}`）+ `category_params`（可选，`{类别: {参数名: ParamSpec}}`），实现 `search()` / `fetch()` / `convert_file()`，`@register` 装饰，加入 `__init__.py` 导入。CLI 子命令参数、回退链、`--list-providers`、配置段自动生成。
- **新增类别**：任何 `<域>.<操作>` 格式的类别（如 `search.video`）注册即生效——`search_categories()` 自动生成子命令。
- 类别注册校验：name 非空且唯一、类别名合法（`<域>.<操作>`）、同类别参数名不冲突，违规注册直接报错。

## 资源导航（按需读取）

| 需要 | 读 |
|---|---|
| 配置项大全 / 配置命令 / 限流配额 | `references/configuration.md` |
| 11 个 provider 类别声明总表 / 参数归属 / paper 细节 / 输出格式 | `references/backends.md` |
| 安装 / 测试 / 更新 / 架构（开发者） | `references/development.md` |
| 代码（repo 即 skill 的实现） | `script/`（`cd script && uv tool install ".[local]"` 安装，[local] 带 pdf-inspector） |

## Notes

- **零依赖**纯标准库；输出无 `--json`，唯一格式就是 Markdown。
- **网络**：jina/firecrawl 不可达时回退链自动跳过（默认超时 10s/60s），全部失败 exit 1。
- **破坏性重构 v0.2.0**：旧命令 `paper` / `fetch` / `tags`（顶层）与旧参数 `--backend` / `--image` / `--tag`（通用 search）已移除，无兼容层。
