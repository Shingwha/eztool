# eztool v2 重构设计文档

> 版本：0.2.0（破坏性重构，**不向后兼容**）
> 状态：设计定稿，待实施
> 核心转变：命令面从「6 个交叉维度顶层命令」→「域（domain）+ 子命令」双层结构；
> provider 从「粗粒度能力 + 全局参数并集」→「类别声明 + 按类别参数归属」。

---

## 1. 背景与动机

### 1.1 现状问题（v0.1.1）

1. **命令面维度交叉**：`search`（通用）/`paper`（论文）按内容类型分，`fetch`（URL）/`convert`（文件）按输入来源分——用户需自行判断每个需求属于哪个维度。
2. **参数拼凑**：`search` 的参数面 = 6 个搜索 provider 的 `search_params` 并集。`--image` 仅 doubao 认、`--tag` 仅 anysearch 认、`--year` 仅论文源认，却在同一命令平铺。
3. **路由不按参数过滤**（`--image` 静默失效的根因）：auto 回退链只检查 `capability == "search"`，不检查参数归属。anysearch 同样声明 `search`，会静默接走 `--image` 请求并返回文字结果，无报错无降级。
4. **`tags` 占顶层命令位**：仅是 anysearch 一个 provider 的附属常量。
5. **`--backend` 与 `--providers` 功能重复**：两个参数都接收逗号分隔 provider 名列表。

### 1.2 重构目标

- 命令面清晰：**域（顶层 3 个）+ 子命令（搜索类别）**，每个子命令参数面定制、意图自解释
- provider 可扩展：**加类别/加 provider 全部声明式**，公共代码零改动
- 路由准确：回退链按**类别**过滤，参数-后端错配从模型上消失
- 代码简洁：去掉全部兼容层（alias、过渡提示、旧参数），破坏性重构

---

## 2. 设计原则

1. **一个域一个动词，类别作子命令**：顶层只放域（search/convert/config），搜索类别（web/image/paper/data）是 search 的子命令。顶层空间留给"域"而非"类别"，类别无限扩展不涨顶层。
2. **需要区分的用子命令，不需要区分的自动识别**：搜索意图无法从查询词推断 → 显式子命令；URL 与本地路径不会混淆 → convert 自动识别。
3. **类别即路由单元**：provider 声明 `categories`，回退链按类别过滤，命令参数按类别归属——三者由同一张注册表驱动。
4. **不向后兼容**：无 alias、无旧参数兼容、无过渡提示。旧命令（paper/fetch/tags）与旧参数（--backend/--image/--paper/--tag）直接消失。
5. **声明式扩展**：新增类别 = registry 注册一个类别 + provider 声明；新增 provider = 声明 categories + category_params。CLI 子命令、参数面、回退链、凭证检查全部自动生成。
6. **业务逻辑零丢弃**：HTTP 层、错误分类、回退链 failover 机制、6 个搜索 provider 的请求/解析逻辑全部复用，只改"如何被路由"。

---

## 3. 目标架构

### 3.1 命令树

```
eztool
├── search                         搜索域
│   ├── web <query>                通用网页搜索
│   │     [--providers a,b] [--count N] [--full] [--timeout N]
│   ├── image <query>              图片搜索
│   │     [--providers a,b] [--width-min N] [--width-max N]
│   │     [--height-min N] [--height-max N] [--shapes 横长方形|竖长方形|方形]
│   │     [--count N] [--timeout N]
│   ├── paper <query>              论文搜索
│   │     [--providers a,b] [--year Y] [--author NAME]
│   │     [--sort relevance|cited|date] [--oa] [--count N] [--full] [--timeout N]
│   ├── data <query>               专业数据源
│   │     [--tag T] [--providers a,b] [--count N] [--timeout N]
│   └── tags                       列出全部数据源标签（无参数）
│
├── convert <url|文件>             转换域：一切 → Markdown
│     [--out PATH] [--providers a,b] [--timeout N] [--list-providers]
│     （自动识别：http(s):// → 在线抓取链；本地路径 → 本地解析链）
│
├── config                         配置域
│     ├── show                     显示全部配置 + 配置文件路径
│     ├── set <key> [value]        设置（省略值交互输入）
│     ├── get <key>                读取（secret 脱敏）
│     ├── reset <key>              重置默认值
│     ├── test [--providers]       验证凭证
│     └── clear                    删除配置文件
│
└── (未来) download                媒体下载域（yt-dlp）——预留，本期不实施
```

### 3.2 命令职责边界

| 命令 | 输入 | 输出 | 一句话 |
|---|---|---|---|
| `search web` | 查询词 | 网页结果列表 / AI 合成回答 | 通用信息查询 |
| `search image` | 查询词 | 图片结果（直链 + 尺寸/形状元数据） | 图片查询 |
| `search paper` | 查询词 | 论文卡片（作者/年份/引用/DOI/OA） | 学术查询 |
| `search data` | 查询词 | 专业数据源结果 | 结构化数据查询 |
| `search tags` | — | 数据源标签清单 | 看有什么数据源 |
| `convert` | URL 或文件路径 | Markdown 文本（或 --out 文件） | 把内容读成文本 |
| `config` | 键值 | 配置状态 | 管理工具 |

---

## 4. Provider 模型设计

### 4.1 类别（category）定义

类别是**路由与参数归属的最小单元**。命名：`<域>.<操作>`。

| 类别 | 含义 | 命令 |
|---|---|---|
| `search.web` | 通用网页搜索 | `search web` |
| `search.image` | 图片搜索 | `search image` |
| `search.paper` | 论文搜索 | `search paper` |
| `search.data` | 专业数据源 | `search data` |
| `convert.page` | URL → Markdown | `convert <url>` |
| `convert.file` | 本地文件 → Markdown | `convert <文件>` |

### 4.2 Provider 基类改造

```python
class Provider:
    name: str
    categories: frozenset[str]                    # 支持哪些类别（可多类别）
    category_params: dict[str, list[ParamSpec]]   # 每类别的 provider 特有参数
    # 原 search_params / capabilities 移除

    # 能力方法按需实现：
    def search(self, query, opts) -> SearchResponse   # search.* 类别
    def fetch(self, url, opts) -> FetchResult         # convert.page
    def convert_file(self, path, opts) -> FetchResult # convert.file
```

### 4.3 11 个 provider 类别声明（总表）

| provider | search.web | search.image | search.paper | search.data | convert.page | convert.file | category_params |
|---|---|---|---|---|---|---|---|
| doubao | ✅ | ✅ | | | | | image: width_min, width_max, height_min, height_max, shapes |
| anysearch | ✅ | | | ✅ | | | data: tag |
| deepseek | ✅ | | | | | | — |
| openalex | | | ✅ | | | | — |
| arxiv | | | ✅ | | | | — |
| crossref | | | ✅ | | | | — |
| markdown_new | | | | | ✅ | ✅ | — |
| jina_reader | | | | | ✅ | | — |
| firecrawl | | | | | ✅ | | — |
| mineru | | | | | ✅ | ✅ | — |
| pdfinspector | | | | | | ✅ | — |

**此表是架构核心产物**：registry 注册、CLI 子命令生成、回退链、参数面、`--list-providers` 全部由它驱动。

### 4.4 参数分类

三层参数，各有归属：

| 层 | 参数 | 定义位置 |
|---|---|---|
| 通用层 | `--timeout`（search/convert 均有） | 命令层 |
| 类别共享层 | `--count`（web/image/paper/data）、`--full`（web/paper）、`--providers`、`--out`、`--list-providers` | 命令层按类别适用性配置 |
| provider 特有层 | `--width-min/--width-max/--height-min/--height-max/--shapes`（doubao image）、`--tag`（anysearch data） | provider 的 category_params，注册表自动并入该类别子命令 |

命令专属参数（不属于任何 provider）：`search paper` 的 `--year/--author/--sort/--oa`，由 api.paper 统一处理。

### 4.5 参数适用性矩阵

| 参数 | web | image | paper | data | tags | convert |
|---|---|---|---|---|---|---|
| `--providers` | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `--count` | ✅ | ✅ | ✅ | ✅ | — | — |
| `--full` | ✅ | — | ✅ | — | — | — |
| `--timeout` | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `--width-min/--width-max/--height-min/--height-max/--shapes` | — | ✅ | — | — | — | — |
| `--tag` | — | — | — | ✅ | — | — |
| `--year/--author/--sort/--oa` | — | — | ✅ | — | — | — |
| `--out` | — | — | — | — | — | ✅ |
| `--list-providers` | — | — | — | — | — | ✅ |

---

## 5. Registry 设计（registry.py 重写）

```python
CATEGORIES: dict[str, list[str]] = {}   # category -> [provider 名]（注册顺序）

def register(cls) -> cls:
    # 校验 name 非空、name 唯一、category 名合法（<域>.<操作>）
    # 登记 categories 中每个类别 → CATEGORIES[cat].append(cls.name)

def providers_for(category: str) -> list[str]
    # 回退链候选；未知类别 → ServiceError(CATEGORY_INVALID)

def category_params(category: str) -> dict[str, ParamSpec]
    # 该类别所有 provider 的 category_params 并集 → CLI 生成 argparse

def search_categories() -> list[str]
    # 全部 search.* 类别（排序）→ CLI 生成 search 子命令

def convert_page_services() / convert_file_services() -> list[str]
    # convert 路由用（--list-providers 也消费）

def service_names() -> list[str]        # 保留
def create_service(name, opts)          # 保留
```

---

## 6. Chain 设计（chain.py 改动）

`run_chain(names, capability, invoke, opts, log)` 的第二个参数语义从 `capability` 改为 `category`：

```python
# 过滤条件变化（一行）：
if category not in svc.categories:      # 原：if capability not in svc.capabilities
    log(f"[{name}] skipped: no '{category}'")
    continue
```

其余 failover 语义、stderr 日志格式（`[provider] OK (耗时, 结果数)`）、全部失败返回 None 均保留。`run_fanout`（多源并行汇总）保留，用于 `search paper` 的 openalex+arxiv+crossref 并行。

---

## 7. API 层设计（api.py 改动）

```python
def search_category(cfg, category: str, query: str, opts: dict) -> SearchResponse:
    """按类别路由搜索。category 决定回退链与参数面。"""
    if category == "search.paper":
        return _paper(cfg, query, opts)          # 复用现有三源并行逻辑
    # 其余 search.* 类别：run_chain(providers_for(category), category, ...)
    # opts 按该类别参数面解析后传入 provider.search()

def convert(cfg, target: str, opts: dict) -> FetchResult:
    """按输入类型路由转换。"""
    if urlparse(target).scheme in ("http", "https"):
        category, invoke = "convert.page", lambda svc: svc.fetch(target, opts)
    else:
        category, invoke = "convert.file", lambda svc: svc.convert_file(target, opts)
    return run_chain(providers_for(category), category, invoke, ...)
```

现有 provider 的 `search()/fetch()/convert_file()` 方法体**零改动**，只改路由与声明。

---

## 8. CLI 设计（cli.py 重写）

- `search` 子命令 = 遍历 `search_categories()` 自动生成（`search.web/image/paper/data`）+ 手动定义 `tags`。
- 每个子命令的参数 = 命令层通用参数（按 §4.5 适用性）+ `category_params(category)` 自动并入。
- `convert`：单位置参数 `target`；运行时按 §7 规则路由；`--list-providers` 按输入类型（或同时列出两类）输出。
- `config`：保持 v0.1.1 子命令集，`test --backend` 改名 `--providers`。
- **无任何 alias / 兼容分支**。

---

## 9. 配置模型（config.py 改动）

### 9.1 结构

```json
{
  "search": {
    "web":   {"providers": ["doubao", "anysearch", "deepseek"]},
    "image": {"providers": ["doubao"]},
    "paper": {"providers": ["openalex", "arxiv", "crossref"]},
    "data":  {"providers": ["anysearch"]}
  },
  "convert": {
    "page": {"providers": ["markdown_new", "jina_reader", "firecrawl"]},
    "file": {"providers": ["pdfinspector", "markdown_new", "mineru"]}
  },
  "providers": { "<name>": { ... } }
}
```

- `providers.<name>.*`（api_key、timeout 等）**保留不变**。
- 类别段的 `providers` 缺省 = 该类别全部注册 provider 按注册顺序；显式配置则覆盖。
- `_flat_keys()` / `KEY_HINTS` / `SECRET_KEYS` 机制保留，自动展开新结构。

### 9.2 凭证要求

- 匿名可用：openalex / arxiv / crossref / markdown_new / jina / firecrawl / anysearch
- 需 api_key：doubao / deepseek / mineru（v4 可选）
- `config test` 遍历有凭证配置的 provider，`--providers` 可指定单个

---

## 10. 错误处理与输出

| 场景 | 行为 |
|---|---|
| 未知类别 / 未知 provider | ServiceError(CATEGORY_INVALID)，exit code 保持 |
| convert 目标不存在（本地路径） | UsageError，提示路径错误 |
| 类别回退链全部失败 | stderr 输出 `all providers failed`，exit 1 |
| 输出格式 | 统一 Markdown；web/paper 沿用 format_search/format_paper；image/data 新增格式化（图片结果带直链+尺寸，data 带来源标注） |
| stderr 日志 | `[provider] OK (耗时, 结果数)` 保留，可判断实际走的服务 |

---

## 11. 测试策略

| 测试文件 | 覆盖 |
|---|---|
| test_registry.py（新增） | 类别注册、参数归属校验、providers_for、重复类别校验 |
| test_cli.py（新增） | 子命令自动生成、参数面按类别定制、convert URL/文件路由、无 alias 残留 |
| test_search.py（改造） | api.search_category 各类别路由；doubao image 参数透传 |
| test_paper.py（改造） | search paper 三源并行、--sort/--year/--oa 语义 |
| test_convert.py（改造） | convert.page / convert.file 链选择、--out、--list-providers |
| test_config.py（保留+改造） | 类别段配置读写、test --providers |
| test_mineru.py / test_paper_providers.py（保留） | provider 层逻辑原样 |

验证命令：`PYTHONPATH=src python3 -m pytest tests/ -q`

---

## 12. 实施步骤

1. **base.py**：Provider 基类改 `categories` / `category_params`，移除 `capabilities` / `search_params`
2. **registry.py**：类别注册表重写 + 新增 `providers_for` / `category_params` / `search_categories`
3. **providers/\***：11 个文件机械改动类别声明（§4.3 表）
4. **chain.py**：过滤条件 `capability` → `category`
5. **api.py**：新增 `search_category` / 改造 `convert` 路由
6. **cli.py**：命令树重写（子命令自动生成 + tags + convert 自动识别）
7. **config.py**：DEFAULTS 加类别段
8. **formatter.py**：image / data 输出格式化
9. **tests/**：按 §11 改造与新增
10. **SKILL.md / README.md**：全量重写（§13）
11. **版本发布**：0.1.1 → 0.2.0，`uv tool install --force /var/minis/skills/ezwork-tool/script`

---

## 13. SKILL.md 重写大纲

```
# ezwork-tool (eztool)

一个命令完成「搜索 → 转格式 → 管配置」：search（web/image/paper/data/tags）
+ convert（URL 或本地文件 → Markdown）+ config。零依赖纯标准库，repo 即 skill。

## 什么时候用
| 用户想要 | 用 |
| 通用搜索 / AI 回答 | eztool search web "<q>" |
| 图片搜索 | eztool search image "<q>" |
| 论文 / 文献搜索 | eztool search paper "<q>" [--year --sort cited --oa] |
| 行情 / 代码 / CVE 等专业数据 | eztool search data "<q>" --tag <标签> |
| 查看数据源标签 | eztool search tags |
| 读取网页全文 | eztool convert <url> |
| 本地文件转 Markdown | eztool convert <file> [--out out.md] |
| 配置凭证 / 回退链 | eztool config |

## 命令速查 / 安装 / 核心规则（类别路由、回退链、凭证、输出）
## Provider 扩展指南（如何加类别 / 加 provider —— 声明式示例）
```

---

## 14. 验收标准

1. `eztool --help` 只展示 search / convert / config 三个域，子命令与参数由注册表驱动且按类别定制
2. `eztool search image "猫"` 只路由 doubao，返回图片直链 + 尺寸/形状元数据（**无 --image 参数、无静默失效**）
3. `eztool search paper` 三源并行，`--sort cited --year 2023` 生效
4. `eztool search data "AAPL" --tag finance.quote` 返回行情
5. `eztool convert <url>` 走 convert.page 链（markdown_new→jina→firecrawl）；`eztool convert <file>` 走 convert.file 链（pdfinspector→markdown_new→mineru）
6. 全代码无 `--backend` / `--image` / `--paper` / `--tag` 残留参数，无 alias 分支
7. 96 个旧测试适配后全绿 + 新增测试全绿
8. `--list-providers` 按类别输出 provider 及能力

---

## 15. 风险与边界

- **范围**：只重构命令面、路由、注册表；HTTP 层 / 错误分类 / provider 请求解析逻辑不动
- **风险**：search paper 的三源并行逻辑（api.paper）改造中需保持 --sort 两阶段排序语义不变
- **边界**：download（yt-dlp）不在本期；`--format json` 结构化输出不在本期（留接口位）

---

*设计定稿：2026-08-03。待用户确认后按 §12 实施。*
