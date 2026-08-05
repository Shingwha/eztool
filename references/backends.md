# 后端能力参考（backends.md）

> 需要精确了解某个 provider 支持什么类别、输出什么、覆盖范围时读本文件。SKILL.md 只保留"怎么选类别"的速览。

## 11 个 provider 类别声明（总表）

类别是路由与参数归属的最小单元（`<域>.<操作>`）：回退链按类别过滤、CLI 子命令参数面按类别生成，全部由 `registry.CATEGORIES` 驱动。

| provider | search.web | search.image | search.paper | search.data | convert.page | convert.file | category_params |
|---|---|---|---|---|---|---|---|
| `doubao` | ✅ | ✅ | | | | | image: width_min/width_max/height_min/height_max/shapes |
| `anysearch` | ✅ | | | ✅ | | | data: tag / params |
| `deepseek` | ✅ | | | | | | — |
| `openalex` | | | ✅ | | | | —（year/author/sort/oa 是 paper 命令专属参数） |
| `arxiv` | | | ✅ | | | | — |
| `crossref` | | | ✅ | | | | — |
| `markdown_new` | | | | | ✅ | ✅ | — |
| `jina_reader` | | | | | ✅ | | — |
| `firecrawl` | | | | | ✅ | | — |
| `mineru` | | | | | ✅ | ✅ | — |
| `anydoc` | | | | | | ✅ | —（本地文档解析，无凭证；14 格式 + 纯文本/HTML） |

> v0.2.0 起 provider 名 `markdown` / `jina` 更名为 `markdown_new` / `jina_reader`（与模块名对齐，破坏性变更）。

## 搜索类别速览

| 类别 | 命令 | 回退链（默认） | 凭证 | 输出 |
|---|---|---|---|---|
| `search.web` | `search web` | doubao → anysearch → deepseek | doubao/deepseek 需 key | 网页结果 / AI 合成回答 |
| `search.image` | `search image` | doubao | 需要 | 图片直链 + 尺寸/形状 |
| `search.paper` | `search paper` | openalex + arxiv + crossref（**并行**汇总） | 无 | 论文卡片 |
| `search.data` | `search data` | anysearch | 可选（匿名可用） | 数据源结果 + 来源标注 |
| `convert.page` | `convert <url>` | markdown_new → jina_reader → firecrawl | 无 | 网页全文 Markdown |
| `convert.file` | `convert <file>` | anydoc → markdown_new → mineru | 无（mineru v4 可选） | 文件内容 Markdown |

## 回退链规则

- 默认按类别注册顺序逐个尝试，失败自动换下一个（failover）；`--providers a,b` 临时覆盖链，`config set search.web.providers ...` 永久覆盖。
- 传了不支持该类别的 provider（如给 `search image` 传 anysearch）→ 链自动跳过（stderr `skipped: no 'search.image'`）。
- `search paper` 是 fan-out：所有源**并行**搜索、按 DOI→URL→标题归一化去重（first wins）、每条标注来源。
- 未知 provider 名 → 用法错误（exit 2）；全部失败 → `all providers failed`（exit 1）。

## 参数归属

- **类别共享**：`--providers`（web/image/paper/data/convert）、`--count`（web/image/paper/data，默认值已合理，如非必要不加）、`--timeout`（除 tags 外全部）。
- **paper 命令专属**（不属于任何 provider）：`--year` / `--author` / `--sort` / `--oa`，由 api 统一透传给三源。
- **provider 特有**（注册表自动并入对应子命令）：`search image` 的 `--width-min/--width-max/--height-min/--height-max/--shapes`（doubao）、`search data` 的 `--tag`（anysearch）。
- 参数面按类别定制，错配参数从 CLI 模型上消失；程序化调用传错归属仍会报错（exit 2），不静默忽略。

## 论文搜索（search paper）细节

```bash
eztool search paper "vision transformer"                    # 默认三源并行：openalex+arxiv+crossref
eztool search paper "LLM reasoning" --year 2024             # 年份或区间（2020-2024）
eztool search paper "attention" --author Vaswani            # 作者过滤
eztool search paper "survey" --sort cited --count 20        # 相关性候选内按引用排序（relevance/cited/date）
eztool search paper "medical" --oa                          # 仅开放获取
eztool search paper "x" --providers openalex,crossref       # 指定源（或 config set search.paper.providers ...）
```

- 输出论文卡片：标题链接 / 作者（前 3 + et al.）/ 年份 / 期刊 / ⭐引用数 / DOI / OA 直链 / 摘要预览；多源合并时每条带 `[openalex]` 等来源标签，头部显示各源命中数。
- 去重策略：同一 DOI 或同一 URL 或归一化标题只保留第一条（first wins）；openalex 与 crossref 的期刊版本按 DOI 去重，arXiv 预印本（无 DOI）独立保留。
- `--sort cited/date` 是**两阶段排序**：先按相关性取候选集（count×5，50–200 条），再在候选内按引用数/年份重排——避免 API 全局重排把只命中个别词的高引无关论文排最前。
- 覆盖边界：OpenAlex 全学科（期刊+预印本）；arXiv 仅预印本；Crossref 期刊记录。**中文论文（CNKI/万方无公开 API）** 用 `eztool search data "..." --tag academic.search`（anysearch）兜底。

## 输出格式

输出统一为 Markdown：`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + `---` 元数据尾行（backend / total / 耗时 / request_id）。图片结果带直链（`![img](url)`）+ 尺寸/形状；data 结果带来源标注。**结果默认完整输出，不截断**（v0.2.0 起无 `--full`）。无 `--json`，唯一格式就是 Markdown。

## 退出码

0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。
