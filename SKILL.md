---
name: ezwork-tool
description: >-
  Unified CLI for web/image search (Doubao / AnySearch / DeepSeek backends),
  multi-source paper search (OpenAlex / arXiv / Crossref, one `eztool paper`
  command that queries all three in parallel and merges results), URL-to-
  Markdown fetching, and local-file-to-Markdown conversion. Use whenever the
  user asks to search the web (联网搜索 / 豆包 / 火山引擎 / DeepSeek 搜索 /
  查最新信息), search images, search papers / literature (论文搜索 / 查文献 /
  academic), search specialized data sources (code, finance quotes, security
  CVEs, legal, travel, news…), fetch/read the content of a webpage or article
  URL, or convert a local file (PDF/DOCX/XLSX/image/CSV…) to Markdown.
  One command (eztool) covers search AND fetching AND file conversion —
  use it even if the user doesn't name a specific backend.
---

# ezwork-tool (eztool)

一个命令完成「搜索 → 读全文」：`eztool search`（6 个搜索后端）+ `eztool paper`（论文多源汇总搜索）+ `eztool fetch`（URL 转 Markdown）+ `eztool convert`（本地文件转 Markdown）。零依赖、纯标准库，repo 即 skill。

## 什么时候用

| 用户想要 | 用 |
|---|---|
| 联网搜索最新信息（新闻 / 版本发布 / 价格 / 事实核查） | `eztool search "<query>"` |
| 图片搜索 | `eztool search "猫" --image` |
| **论文 / 文献搜索**（含年份/作者/引用排序/开放获取过滤） | `eztool paper "<query>" [--year 2023 --sort cited --oa]` |
| 学术 / 代码 / 金融行情 / CVE / 法律 / 旅行等专业数据 | `eztool search ... --tag <标签>`（先 `eztool tags` 看清单） |
| AI 综合回答 + 来源列表 | `eztool search ... --backend deepseek` |
| 多后端同时搜、合并去重 | `eztool search ... --backend openalex,arxiv` |
| 读取网页 / 文章全文 | `eztool fetch <url>` |
| 本地文件（PDF/DOCX/XLSX/图片/CSV 等）转 Markdown | `eztool convert <file>`（pdfinspector 本地优先 → markdown.new → MinerU 回退） |

## 安装

```bash
cd ezwork-tool/script && uv tool install .           # 基础安装
uv tool install --extra local .                      # 可选：本地 PDF 解析（pdf-inspector）
eztool --version
```

## 快速上手

```bash
eztool search "Rust async 2026"                    # 通用搜索：auto 路由，开箱即用（anysearch 匿名可用）
eztool paper "vision transformer"                  # 论文搜索：openalex+arxiv+crossref 三源并行汇总
eztool paper "LLM reasoning" --year 2024 --sort cited --count 20
eztool fetch https://example.com/article           # URL 全文 → 干净 Markdown（永不截断）
eztool convert report.pdf                          # 本地文件 → Markdown（PDF/DOCX/XLSX/图片…）
eztool search "AAPL" --tag finance.quote           # [anysearch] 专业数据源
eztool tags                                        # 数据源标签清单（40+）
```

## 核心规则

- **后端选择**：`--backend auto`（默认，failover 回退链）｜`--backend <名>`（单个）｜**`--backend a,b`（逗号分隔 = 多后端并行汇总，去重合并）**。
- **论文搜索**：`paper` 默认三源并行（openalex+arxiv+crossref，全部免凭证），输出论文卡片（作者/年份/期刊/⭐引用/DOI/OA），按 DOI 去重；中文论文无公开 API，用 `eztool search "..." --tag academic.search` 兜底。
- **输出**：统一 Markdown；stderr 的 `[provider] OK (耗时, 字数)` 是回退链日志。
- **凭证**：只有 doubao / deepseek 需要配置（`eztool config set providers.<name>.api_key`）；缺凭证报错时引导用户配置，**绝不硬编码密钥**。

## Workflow for the Agent

1. **搜索**：直接 `eztool search "<query>"`。默认 auto 路由即可；用户点名豆包/DeepSeek、需要图片或专业数据源时再显式加参数。
2. **论文 / 文献**：用 `eztool paper "<query>"`（三源并行汇总，免凭证）。`--year` 限年份、`--oa` 找开放获取；多源合并自动按 DOI 去重。中文文献无公开 API，改用 `eztool search "..." --tag academic.search`（anysearch）。
   - **`--sort cited` 如非必要少用**：它先按相关性取候选再按引用重排，多词查询（≥3 个词）时候选内仍可能混入只命中个别词的"泛相关"高引综述（如搜续驶里程估计出现 UAV/自动驾驶综述）。**默认相关性排序最稳**；只有单关键词/短语（如 "transformer"）或用户明确要"高引经典论文"时才用 `--sort cited`。
3. **读全文**：搜索结果里的 URL 用 `eztool fetch <url>` 抓取（输出永不截断；stderr 的 `[provider] OK (耗时, 字数)` 是回退链日志，可判断走的哪个服务）。本地文件（PDF/DOCX/XLSX/图片/CSV/JSON 等）用 `eztool convert <file>` 转 Markdown，`--out` 可写文件；不支持的文件类型/超 10MB 会在本地快速报错（exit 1）。markdown.new 不支持的格式（如 PPTX、GIF/BMP）或转换失败会自动回退到 MinerU（免费无 Token，异步提取，默认开启 OCR，PDF 需 ≤20 页；配置 `providers.mineru.api_key` Token 后自动升级 v4：≤200MB/200 页/支持 doc/ppt/xls/html，输出 zip 自动解出 full.md）。
4. **专业搜索**：先 `eztool tags` 看标签清单，再 `--tag` 定向；部分标签还需 `--params '{"key":"value"}'` 补充参数（如 `code.doc` 需要 `library`）。
5. **凭证缺失**：报 `未配置 XX 凭证` 时，引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
6. **失败处理**：exit 1 = 业务失败（无结果 / API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## 资源导航（按需读取）

| 需要 | 读 |
|---|---|
| 配置项大全 / 配置命令 / 限流配额 | `references/configuration.md` |
| 后端能力矩阵 / 参数归属 / paper 细节 / 输出格式 | `references/backends.md` |
| 安装 / 测试 / 更新 / 架构（开发者） | `references/development.md` |
| 代码（repo 即 skill 的实现） | `script/`（`cd script && uv tool install .` 安装） |

## Notes

- **零依赖**纯标准库；输出无 `--json`，唯一格式就是 Markdown。
- **网络**：jina/firecrawl 不可达时回退链自动跳过（默认超时 10s/60s），全部失败 exit 1。
- **退出码**：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。
