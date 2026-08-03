# 配置参考（configuration.md）

> 当需要设置/查看/排障 eztool 配置时读本文件。SKILL.md 只保留"最少配什么"的速览。

所有配置集中在 `~/.config/ezwork-tool/config.json`（`eztool config show` 首行显示实际路径）。

## 配置命令

| 命令 | 作用 |
|---|---|
| `eztool config set <key> [值]` | 设置配置项（省略值则交互输入，密钥隐藏显示） |
| `eztool config get <key>` | 读取单个配置（密钥脱敏） |
| `eztool config show` | 查看全部配置 + 配置文件路径 |
| `eztool config test [--providers <名>]` | 用真实请求验证凭证（默认遍历全部 provider） |
| `eztool config reset <key>` | 恢复默认值 |
| `eztool config clear` | 删除整个配置文件 |

> v0.2.0 起 `config path` 子命令移除（`config show` 首行显示路径）；`test --backend` 改名 `--providers`。

## 各 provider 最少要配什么

| provider | 必须配置 | 不配会怎样 |
|---|---|---|
| `doubao` | `providers.doubao.api_key`，或 `providers.doubao.ak` + `providers.doubao.sk` | `search web` 回退链跳过 doubao（anysearch 匿名兜底）；`search image` 不可用 |
| `anysearch` | 无 | 可用（匿名模式，按 IP 限速）；配 `providers.anysearch.api_key` 可提高配额 |
| `deepseek` | `providers.deepseek.api_key` | `search web` 回退链跳过 deepseek |
| `openalex` / `arxiv` / `crossref` | 无 | 全免费免凭证（OpenAlex 可选配 `providers.openalex.mailto` 进礼貌池） |
| `markdown_new` / `jina_reader` / `firecrawl` | 无 | URL 抓取全免费免 Token；firecrawl/jina 配 key 提高限速 |
| `mineru` | 无 | 走 v1 轻量（≤10MB/20 页）；配 `providers.mineru.api_key` 自动升级 v4 Precision API |
| `pdfinspector` | 无 | 本地 PDF 解析（需 `pip install pdf-inspector`，见安装说明）；未安装自动跳过 |

## 全部配置项

### providers 段（凭证 / 超时，`providers.<name>.*`）

| 键 | 默认 | 说明 |
|---|---|---|
| `providers.doubao.api_key` | 空 | 豆包 WebSearch API Key（Bearer） |
| `providers.doubao.ak` / `providers.doubao.sk` | 空 | 火山引擎 AK/SK（与 api_key 二选一） |
| `providers.doubao.auth` | 自动 | 鉴权方式 `apikey` / `aksk`，留空自动检测 |
| `providers.doubao.count_web` | 10 | 网页结果数（≤50） |
| `providers.doubao.count_image` | 5 | 图片结果数（≤5） |
| `providers.doubao.need_url` / `need_content` | false | 只返回带落地链接 / 带正文的结果 |
| `providers.doubao.content_formats` | 空 | 正文格式 `text` / `markdown` |
| `providers.doubao.time_range` | 空 | 时间范围 `OneDay`/`OneWeek`/`OneMonth`/`OneYear` 或 `YYYY-MM-DD..YYYY-MM-DD` |
| `providers.doubao.industry` | 空 | 行业搜索 `finance` / `game` / `gov` |
| `providers.doubao.timeout` | 30 | doubao 请求超时（秒） |
| `providers.anysearch.api_key` | 空 | 可选；不配则匿名 |
| `providers.anysearch.max_results` | 10 | 结果数（≤20） |
| `providers.deepseek.api_key` | 空 | 必填才可用 deepseek |
| `providers.deepseek.model` | `deepseek-v4-flash` | 模型 |
| `providers.deepseek.thinking` | `enabled` | 思考模式 `enabled`/`disabled`（enabled 更准但更慢更贵） |
| `providers.deepseek.max_tokens` | 32768 | 最大输出 token 数 |
| `providers.openalex.mailto` | 空 | OpenAlex 礼貌池邮箱（推荐填，提升限流配额） |
| `providers.openalex.timeout` | 30 | openalex 请求超时（秒） |
| `providers.arxiv.timeout` | 30 | arxiv 请求超时（秒） |
| `providers.crossref.timeout` | 30 | crossref 请求超时（秒） |
| `providers.firecrawl.api_key` | 空 | 可选，提高 firecrawl 限速 |
| `providers.jina_reader.api_key` | 空 | 可选，提高 jina 限速（无 key 约 20 RPM） |
| `providers.markdown_new.timeout` | 30 | markdown.new 超时（秒） |
| `providers.mineru.api_key` | 空 | MinerU Token（可选）：配了走 v4 Precision API（≤200MB/200页/批量/HTML），不配走 v1 轻量（≤10MB/20页） |
| `providers.mineru.timeout` | 300 | MinerU 提取任务总超时（秒，提交+轮询+下载） |
| `providers.pdfinspector.timeout` | 60 | pdfinspector 本地解析超时（秒） |

### 类别段（回退链 / 缺省超时）

| 键 | 默认 | 说明 |
|---|---|---|
| `search.web.providers` | `doubao,anysearch,deepseek` | 网页搜索回退链（逗号分隔，按序尝试，首个成功即用） |
| `search.web.timeout` | 30 | 网页搜索默认超时（秒） |
| `search.image.providers` | `doubao` | 图片搜索回退链 |
| `search.image.timeout` | 30 | 图片搜索默认超时（秒） |
| `search.paper.providers` | `openalex,arxiv,crossref` | 论文搜索源列表（逗号分隔；并行搜全部源并去重合并） |
| `search.paper.timeout` | 30 | 论文搜索默认超时（秒） |
| `search.data.providers` | `anysearch` | 专业数据源回退链 |
| `search.data.timeout` | 30 | 专业数据源默认超时（秒） |
| `convert.page.providers` | `markdown_new,jina_reader,firecrawl` | URL → Markdown 抓取链（免费优先，按序尝试） |
| `convert.page.timeout` | 30 | URL 抓取默认超时（秒） |
| `convert.file.providers` | `pdfinspector,markdown_new,mineru` | 文件 → Markdown 转换链（本地解析优先；MinerU 支持 PPTX/老格式图片等，异步轮询较慢） |
| `convert.file.timeout` | 60 | 文件转换默认超时（秒） |

> 类别段 `providers` 缺省 = 该类别的注册顺序（registry），显式配置则覆盖。`config set` / `reset` / `get` / `show` 对以上全部键生效，键名自动展开，无需记忆清单（`eztool config show` 输出全部）。

## 限流 / 配额

- doubao 5 QPS、免费 500 次/月
- anysearch 匿名按 IP 限速
- deepseek 每次约 8k–15k token
- jina 无 key 约 20 RPM
- openalex 免费无 key；配 mailto 进礼貌池后配额更高（10 万次/天量级）
