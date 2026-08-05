# 后端能力参考（backends.md）

> 需要精确了解某个 provider 支持什么类别、输出什么、覆盖范围时读本文件。SKILL.md 只保留"怎么选类别"的速览。

## 9 个 provider 类别声明（总表）

类别是路由与参数归属的最小单元（`<域>.<操作>`）：回退链按类别过滤、CLI 子命令参数面按类别生成，全部由 `registry.CATEGORIES` 驱动。

| provider | search.web | search.image | search.data | convert.page | convert.file | category_params |
|---|---|---|---|---|---|---|
| `doubao` | ✅ | ✅ | | | | image: width_min/width_max/height_min/height_max/shapes |
| `anysearch` | ✅ | | ✅ | ✅ | | data: tag / params |
| `deepseek` | ✅ | | | | | — |
| `markdown_new` | | | | ✅ | ✅ | — |
| `jina_reader` | | | | ✅ | | — |
| `firecrawl` | | | | ✅ | | — |
| `mineru` | | | | ✅ | ✅ | — |
| `anydoc` | | | | | ✅ | —（本地文档解析，无凭证；14 格式 + 纯文本/HTML） |
| `tavily` | ✅ | | | ✅ | | — |
| `exa` | ✅ | | | ✅ | | — |

> v0.2.0 起 provider 名 `markdown` / `jina` 更名为 `markdown_new` / `jina_reader`（与模块名对齐，破坏性变更）。

## 搜索类别速览

| 类别 | 命令 | 回退链（默认） | 凭证 | 输出 |
|---|---|---|---|---|
| `search.web` | `search web` | doubao → anysearch → deepseek | doubao/deepseek 需 key | 网页结果 / AI 合成回答 |
| `search.image` | `search image` | doubao | 需要 | 图片直链 + 尺寸/形状 |
| `search.data` | `search data` | anysearch | 可选（匿名可用） | 数据源结果 + 来源标注 |
| `convert.page` | `convert <url>` | markdown_new → jina_reader → anysearch → tavily → firecrawl | 无（anysearch/tavily key 可选） | 网页全文 Markdown |
| `convert.file` | `convert <file>` | anydoc → markdown_new → mineru | 无（mineru v4 可选） | 文件内容 Markdown |

## 回退链规则

- 默认按类别注册顺序逐个尝试，失败自动换下一个（failover）；`--providers a,b` 临时覆盖链，`config set search.web.providers ...` 永久覆盖。
- 传了不支持该类别的 provider（如给 `search image` 传 anysearch）→ 链自动跳过（stderr `skipped: no 'search.image'`）。
- **质量门**（URL 抓取特有）：每个 provider 的抓取结果过通用质量门（`quality.py`）——内容开头 200 字符命中拦截话术（环境异常/完成验证/captcha/just a moment/attention required 等）且内容 < 800 字符 → 判为反爬/验证页（`blocked`），链继续回退；命中但 800–1500 字符 → 可疑，记为后备继续尝试，全部低质时返回后备 + 警告；≥1500 字符或未命中 → 正常返回。短新闻等无拦截话术的内容不受影响。微信公众号验证页即典型"假成功"（HTTP 200 + 非空），现被自动拦截并回退到 tavily。
- 未知 provider 名 → 用法错误（exit 2）；全部失败 → `all providers failed`（exit 1）。

## 参数归属

- **类别共享**：`--providers`（web/image/data/convert）、`--count`（web/image/data，默认值已合理，如非必要不加）、`--timeout`（除 tags 外全部）。
- **provider 特有**（注册表自动并入对应子命令）：`search image` 的 `--width-min/--width-max/--height-min/--height-max/--shapes`（doubao）、`search data` 的 `--tag` / `--params`（anysearch）。
- 参数面按类别定制，错配参数从 CLI 模型上消失；程序化调用传错归属仍会报错（exit 2），不静默忽略。

## 输出格式

输出统一为 Markdown：`### Answer`（deepseek 回答，可选）+ `### Results (N)` 编号列表 + `---` 元数据尾行（backend / total / 耗时 / request_id）。图片结果带直链（`![img](url)`）+ 尺寸/形状；data 结果带来源标注。**结果默认完整输出，不截断**（v0.2.0 起无 `--full`）。无 `--json`，唯一格式就是 Markdown。

## 退出码

0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。错误在 stderr，格式 `error: <原因>` + `code: <语义码>`。
