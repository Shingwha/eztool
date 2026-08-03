# 后端能力参考（backends.md）

> 需要精确了解某个后端支持什么参数、输出什么、覆盖范围时读本文件。SKILL.md 只保留"怎么选后端"的速览。

## 六个搜索后端

| 后端 | 特点 | 凭证 |
|---|---|---|
| `doubao` | 网页+图片搜索、域名/时间/行业过滤、权威过滤 | 需要（API Key 或 AK/SK） |
| `anysearch` | 40+ 数据源标签（学术/代码/金融/安全/法律/旅行…） | 可选，匿名可用 |
| `deepseek` | 服务端搜索 + AI 合成回答 + 来源列表 | 需要（DeepSeek API Key） |
| `openalex` | 学术论文：250M+ 全学科（期刊+预印本）、年份/作者/OA 过滤、引用排序 | 无 |
| `arxiv` | 预印本（CS/物理/数学/生/经），快而干净 | 无 |
| `crossref` | 期刊 DOI 元数据（含引用数），兜底 | 无 |

## 能力矩阵

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

## 后端选择规则

- `--backend auto`（默认）：按 `search.providers` 链逐个尝试（免费优先，失败的自动换下一个，failover）；顺序可用配置或 `--providers` 临时覆盖。
- `--backend doubao|anysearch|deepseek|openalex|arxiv|crossref`：强制指定单个。
- **`--backend a,b,c` 逗号分隔 = 多后端并行汇总**：同时搜、按 DOI→URL→标题归一化去重（first wins）、每条标注来源。anysearch 匿名可用，所以**开箱即可搜索**。
- `eztool search "q" --backend openalex,arxiv` 与 `eztool paper "q"` 的区别：paper 是论文专用入口（默认三论文源 + 论文卡片输出），search 是通用入口（任何后端组合）。

## 参数归属（参数传给不支持的（单个）后端 → 报错 exit 2，不静默忽略）

- 仅 `anysearch`：`--tag` / `--zone` / `--language` / `--params` / `--anonymous`
- 仅 `doubao`：`--image` / `--sites` / `--block-hosts` / `--time-range` / `--need-content` / `--need-url` / `--content-formats` / `--industry` / `--query-rewrite` / `--auth-info-level` / `--width-*` / `--height-*` / `--shapes`
- 仅 `openalex`（search 命令下；paper 命令三源全通）：`--year` / `--author` / `--sort` / `--oa`
- 公共：`--count` / `--timeout` / `--full`（search/paper 用 `--backend` 选后端，fetch/convert 用 `--providers` 覆盖回退链）

## 论文搜索（paper）细节

```bash
eztool paper "vision transformer"                      # 默认三源并行：openalex+arxiv+crossref
eztool paper "LLM reasoning" --year 2024               # 年份或区间（2020-2024）
eztool paper "attention" --author Vaswani              # 作者过滤
eztool paper "survey" --sort cited --count 20          # 相关性候选内按引用排序（relevance/cited/date）
eztool paper "medical" --oa                            # 仅开放获取
eztool paper "x" --backend openalex,crossref           # 指定源（或 eztool config set paper.providers ...）
```

- 输出论文卡片：标题链接 / 作者（前 3 + et al.）/ 年份 / 期刊 / ⭐引用数 / DOI / OA 直链 / 摘要预览；多源合并时每条带 `[openalex]` 等来源标签，头部显示各源命中数。
- 去重策略：同一 DOI 或同一 URL 或归一化标题只保留第一条（first wins）；openalex 与 crossref 的期刊版本按 DOI 去重，arXiv 预印本（无 DOI）独立保留。
- `--sort cited/date` 是**两阶段排序**：先按相关性取候选集（count×5，50–200 条），再在候选内按引用数/年份重排——避免 API 全局重排把只命中个别词的高引无关论文（如 DESeq2）排最前。
- 覆盖边界：OpenAlex 全学科（期刊+预印本）；arXiv 仅预印本；Crossref 期刊记录。**中文论文（CNKI/万方无公开 API）** 用 `eztool search "..." --tag academic.search`（anysearch）兜底。
- 全部免凭证；`paper.providers` 可配置默认源列表。

## 输出格式

输出统一为 Markdown：`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + `---` 元数据尾行（backend / total / 耗时 / request_id）。无 `--json`，唯一格式就是 Markdown。

## 退出码

0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。
