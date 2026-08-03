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
| 本地文件（PDF/DOCX/XLSX/图片/CSV 等）转 Markdown | `eztool convert <file>`（markdown.new→MinerU 回退，≤10MB） |

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
| `doubao` | `providers.doubao.api_key`，或 `providers.doubao.ak` + `providers.doubao.sk` | 无法使用 doubao 后端（auto 会尝试其他后端） |
| `anysearch` | 无 | 可用（匿名模式，按 IP 限速）；配 `providers.anysearch.api_key` 可提高配额 |
| `deepseek` | `providers.deepseek.api_key` | 无法使用 deepseek 后端 |
| `openalex` / `arxiv` / `crossref` | 无 | 全免费免凭证（OpenAlex 可选配 `providers.openalex.mailto` 进礼貌池） |
| `fetch` / `convert` | 无 | 可用（markdown.new→jina→firecrawl 抓取、markdown.new→MinerU 转换，全免费无 Token）；配 `mineru.api_key` 后 MinerU 自动升级 v4 Precision API |

### 全部配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `providers.doubao.api_key` | 空 | 豆包 WebSearch API Key（Bearer） |
| `providers.doubao.ak` / `providers.doubao.sk` | 空 | 火山引擎 AK/SK（与 api_key 二选一） |
| `providers.doubao.auth` | 自动 | 鉴权方式 `apikey` / `aksk`，留空自动检测 |
| `providers.doubao.count_web` | 10 | 网页结果数（≤50） |
| `providers.doubao.count_image` | 5 | 图片结果数（≤5） |
| `providers.doubao.need_url` | false | 只返回带落地链接的结果 |
| `providers.doubao.need_content` | false | 只返回带正文的结果 |
| `providers.doubao.content_formats` | 空 | 正文格式 `text` / `markdown` |
| `providers.doubao.time_range` | 空 | 时间范围 `OneDay`/`OneWeek`/`OneMonth`/`OneYear` 或 `YYYY-MM-DD..YYYY-MM-DD` |
| `providers.doubao.industry` | 空 | 行业搜索 `finance` / `game` / `gov` |
| `providers.doubao.timeout` | 30 | 请求超时（秒） |
| `providers.anysearch.api_key` | 空 | 可选；不配则匿名 |
| `providers.anysearch.max_results` | 10 | 结果数（≤20） |
| `providers.deepseek.api_key` | 空 | 必填才可用 deepseek 后端 |
| `providers.deepseek.model` | `deepseek-v4-flash` | 模型 |
| `providers.deepseek.thinking` | `enabled` | 思考模式 `enabled`/`disabled`（enabled 更准但更慢更贵） |
| `providers.deepseek.max_tokens` | 32768 | 最大输出 token 数 |
| `providers.openalex.mailto` | 空 | OpenAlex 礼貌池邮箱（推荐填，提升限流配额） |
| `providers.openalex.timeout` | 30 | openalex 请求超时（秒） |
| `providers.arxiv.timeout` | 30 | arxiv 请求超时（秒） |
| `providers.crossref.timeout` | 30 | crossref 请求超时（秒） |
| `paper.providers` | `openalex,arxiv,crossref` | 论文搜索源列表（逗号分隔；`paper` 命令并行搜全部源并去重合并） |
| `paper.timeout` | 30 | 论文搜索默认超时（秒） |
| `search.providers` | `anysearch,doubao,deepseek` | 搜索回退链（逗号分隔，免费优先，按序尝试，首个成功即用） |
| `search.timeout` | 30 | 搜索默认超时（秒） |
| `fetch.providers` | `markdown,jina,firecrawl` | 抓取回退链（逗号分隔，免费优先，按序尝试，首个成功即用） |
| `fetch.timeout` | 30 | 抓取默认超时（秒） |
| `providers.firecrawl.api_key` | 空 | 可选，提高 firecrawl 限速 |
| `providers.jina.api_key` | 空 | 可选，提高 jina 限速（无 key 约 20 RPM） |
| `convert.providers` | `markdown,mineru` | 文件转换回退链（逗号分隔，MinerU 支持 PPTX/老格式图片等，异步轮询较慢） |
| `convert.timeout` | 60 | 文件转换默认超时（秒） |
| `providers.markdown.timeout` | 30 | markdown.new 超时（秒） |
| `providers.mineru.timeout` | 300 | MinerU 提取任务总超时（秒，提交+轮询+下载） |
| `providers.mineru.api_key` | 空 | MinerU Token（可选）：配了走 v4 Precision API（≤200MB/200页/批量/HTML），不配走 v1 轻量（≤10MB/20页） |

## 搜索：六个后端能搜什么

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
| 论文搜索（含引用排序） | ❌ | ❌ | ❌ |
| 凭证要求 | 必须 | 可选（匿名可用） | 必须 |

> openalex / arxiv / crossref 三个论文后端（免凭证）：`eztool paper` 是它们的主入口（三源并行汇总）；`eztool search "q" --backend openalex` 也可直接用。论文特有参数 `--year / --author / --sort / --oa` 归 openalex 声明，`search` 命令下仅 openalex 后端可用，`paper` 命令下三源全通。

后端选择：`--backend auto`（默认）按 `search.providers` 链逐个尝试（免费优先，失败的自动换下一个，failover）；顺序可用配置或 `--providers` 临时覆盖；`--backend doubao|anysearch|deepseek|openalex|arxiv|crossref` 强制指定单个；**`--backend a,b,c` 逗号分隔 = 多后端并行汇总**（同时搜、按 DOI→URL→标题归一化去重、first wins、每条标注来源）。anysearch 匿名可用，所以**开箱即可搜索**。

## 论文搜索（paper）

```bash
eztool paper "vision transformer"                      # 默认三源并行：openalex+arxiv+crossref
eztool paper "LLM reasoning" --year 2024               # 年份或区间（2020-2024）
eztool paper "attention" --author Vaswani              # 作者过滤
eztool paper "survey" --sort cited --count 20          # 相关性候选内按引用排序（relevance/cited/date）
eztool paper "medical" --oa                            # 仅开放获取
eztool paper "x" --backend openalex,crossref     # 指定源（或 eztool config set paper.providers ...）
```

- 输出论文卡片：标题链接 / 作者（前 3 + et al.）/ 年份 / 期刊 / ⭐引用数 / DOI / OA 直链 / 摘要预览；多源合并时每条带 `[openalex]` 等来源标签，头部显示各源命中数。
- 去重策略：同一 DOI 或同一 URL 或归一化标题只保留第一条（first wins）；openalex 与 crossref 的期刊版本按 DOI 去重，arXiv 预印本（无 DOI）独立保留。
- `--sort cited/date` 是**两阶段排序**：先按相关性取候选集（count×5，50–200 条），再在候选内按引用数/年份重排——避免 API 全局重排把只命中个别词的高引无关论文（如 DESeq2）排最前。
- 覆盖边界：OpenAlex 全学科（期刊+预印本）；arXiv 仅预印本；Crossref 期刊记录。**中文论文（CNKI/万方无公开 API）** 用 `eztool search "..." --tag academic.search`（anysearch）兜底。
- 全部免凭证；`paper.providers` 可配置默认源列表。

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
eztool search "q" --backend openalex,arxiv         # 多后端并行汇总（去重合并+来源标注）

# 论文搜索（三源并行汇总，免凭证）
eztool paper "vision transformer"
eztool paper "LLM reasoning" --year 2024 --sort cited --count 20
eztool paper "attention" --author Vaswani --oa

# 抓取 URL 全文（转干净 Markdown，永不截断）
eztool fetch https://example.com/article
eztool fetch --list-providers

# 本地文件转 Markdown（markdown.new→MinerU 回退链；PDF/DOCX/XLSX/PPTX/图片/CSV/JSON…，≤10MB）
eztool convert report.pdf                  # stdout 输出 Markdown
eztool convert 报告.docx --out report.md   # 写入文件
eztool convert --list-providers

# 数据源标签清单（anysearch 40+ 标签）
eztool tags
```

输出统一为 Markdown：`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + `---` 元数据尾行（backend / total / 耗时 / request_id）。

## Workflow for the Agent

1. **搜索**：直接 `eztool search "<query>"`。默认 auto 路由即可；用户点名豆包/DeepSeek、需要图片或专业数据源时再显式加参数。
2. **论文 / 文献**：用 `eztool paper "<query>"`（三源并行汇总，免凭证）。`--year` 限年份、`--oa` 找开放获取；多源合并自动按 DOI 去重。中文文献无公开 API，改用 `eztool search "..." --tag academic.search`（anysearch）。
   - **`--sort cited` 如非必要少用**：它先按相关性取候选再按引用重排，多词查询（≥3 个词）时候选内仍可能混入只命中个别词的"泛相关"高引综述（如搜续驶里程估计出现 UAV/自动驾驶综述）。**默认相关性排序最稳**；只有单关键词/短语（如 "transformer"）或用户明确要"高引经典论文"时才用 `--sort cited`。
3. **读全文**：搜索结果里的 URL 用 `eztool fetch <url>` 抓取（输出永不截断；stderr 的 `[provider] OK (耗时, 字数)` 是回退链日志，可判断走的哪个服务）。本地文件（PDF/DOCX/XLSX/图片/CSV/JSON 等）用 `eztool convert <file>` 转 Markdown，`--out` 可写文件；不支持的文件类型/超 10MB 会在本地快速报错（exit 1）。markdown.new 不支持的格式（如 PPTX、GIF/BMP）或转换失败会自动回退到 MinerU（免费无 Token，异步提取，默认开启 OCR，PDF 需 ≤20 页；配置 `providers.mineru.api_key` Token 后自动升级 v4：≤200MB/200 页/支持 doc/ppt/xls/html，输出 zip 自动解出 full.md）。
4. **专业搜索**：先 `eztool tags` 看标签清单，再 `--tag` 定向；部分标签还需 `--params '{"key":"value"}'` 补充参数（如 `code.doc` 需要 `library`）。
5. **凭证缺失**：报 `未配置 XX 凭证` 时，引导用户 `eztool config set <key>`，**绝不硬编码密钥**；用 `eztool config test` 验证。
6. **失败处理**：exit 1 = 业务失败（无结果 / API 错误），exit 2 = 用法或凭证问题；错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。

## Notes

- **零依赖**纯标准库；输出无 `--json`，唯一格式就是 Markdown。
- **参数归属**：`--tag/--zone/--language/--params/--anonymous` 仅 anysearch；`--image/--sites/--block-hosts/--time-range/--need-content/--need-url/--content-formats/--industry/--query-rewrite/--auth-info-level/--width-*/--height-*/--shapes` 仅 doubao；`--year/--author/--sort/--oa` 仅 openalex（search 命令下；paper 命令三源全通）；`--count/--timeout/--full` 公共（search/paper 用 `--backend` 选后端，fetch/convert 用 `--providers` 覆盖回退链）。参数传给不支持的后端 → 报错 exit 2，不静默忽略。
- **限流 / 配额**：doubao 5 QPS、免费 500 次/月；anysearch 匿名按 IP 限速；deepseek 每次约 8k–15k token；jina 无 key 约 20 RPM。
- **网络**：jina/firecrawl 不可达时回退链自动跳过（默认超时 10s/60s），全部失败 exit 1。
- **退出码**：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。
