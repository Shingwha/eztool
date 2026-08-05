# 开发参考（development.md）

> 开发者视角：安装、测试、更新、架构。agent 一般不需要读本文件，除非要改 eztool 本身。

## 目录结构（repo 即 skill）

```
ezwork-tool/
├── SKILL.md            # 核心使用指引（agent 入口）
├── README.md           # GitHub 门面
├── references/         # 按需加载：configuration.md / backends.md / development.md
└── script/             # 全部代码与构建配置
    ├── pyproject.toml
    ├── src/ezwork_tool/    # 包源码（registry / providers / api / cli / chain / config …）
    └── tests/              # unittest 测试（test_registry / test_cli / test_search / test_paper …）
```

## 安装

```bash
cd ezwork-tool/script
uv tool install ".[local]"    # [local] 额外装 firecrawl-anydoc（本地文档解析，14 格式）
eztool --version
```

## 开发（免安装运行）

```bash
cd script
PYTHONPATH=src python -m ezwork_tool.cli --help    # 免安装运行 CLI
PYTHONPATH=src python -m unittest discover tests -q   # 跑全部测试
```

## 更新（拉取新版本后重装）

```bash
cd ezwork-tool && git pull          # 失败先加仓库代理：git config http.proxy http://127.0.0.1:7890
cd script
uv cache clean ezwork-tool && uv tool install ".[local]" --force --reinstall
# 注意：直接 --force 会复用旧 wheel 缓存装成旧代码，必须先 uv cache clean
# 注意：必须带 [local] extra，否则 firecrawl-anydoc 丢失（uv tool install 没有 --extra 参数）
eztool --help                       # 验证新命令
```

## 架构要点（v0.2.0）

- **类别即路由单元**：`registry.CATEGORIES`（category → provider 列表，注册顺序）驱动 CLI 子命令生成、回退链过滤、参数面、`--list-providers`、配置缺省。类别命名 `<域>.<操作>`：search.web / search.image / search.paper / search.data / convert.page / convert.file。
- **Provider 基类**（base.py）：声明 `name` + `categories`（frozenset）+ `category_params`（`{类别: {参数名: ParamSpec}}`）；实现 `search()` / `fetch()` / `convert_file()`。`@register` 校验 name 唯一、类别名合法、同类别参数不冲突。**HTTP 层与 provider 请求/解析逻辑零改动**，只改声明与路由。
- **公共层**：base.py（Provider 基类 + 数据结构）、http.py（网络工具）、chain.py（run_chain 按类别 failover + run_fanout 并行汇总）、api.py（`search_category` / `convert` 入口）、registry.py（类别注册表）、config.py（providers.<name> + search.<cat> / convert.<cat> 两层配置）、formatter.py（唯一 Markdown 输出，web/paper/image/data 四种格式）。
- **路由**：`api.search_category(cfg, category, query, opts)` 按类别取回退链（paper 走 fan-out 三源并行）；`api.convert(cfg, target, opts)` 按 `urlparse` scheme 自动选 convert.page / convert.file 链。image 类别自动注入 `opts["image"]=True`（doubao 方法体零改动）。
- **参数归属**：`category_params(category)` 并集生成子命令参数面；程序化调用传错归属报 UsageError（exit 2），不静默忽略。
- **配置**：类别段 `providers` 缺省 = registry 注册顺序；`_flat_keys()` 递归展开 DEFAULTS，`KEY_HINTS` / `SECRET_KEYS` 自动对齐。
- **paper 多源汇总**：`_paper` → run_fanout（ThreadPoolExecutor 并行）→ `_merge_search`（DOI→URL→标题归一化去重，first wins，source 回填）。
- **两阶段排序**：openalex/crossref 的 sort=cited/date 先按相关性取候选集（count×5，50–200），客户端重排截断。
- 零依赖纯标准库（Python ≥3.10）。

## 测试策略

- `test_registry.py`：类别注册/校验、providers_for、参数归属、重复类别
- `test_cli.py`：子命令自动生成、参数面按类别定制、无旧参数残留（--backend/--image/--paper）
- `test_search.py`：search_category 各类别路由、image 参数注入、参数归属校验
- `test_paper.py`：三源并行、去重、--sort/--providers 语义
- `test_convert.py`：convert.page / convert.file 路由、--out、--list-providers、缺失路径 UsageError
- `test_config.py` / `test_formatter.py`：配置默认值 / image / data 输出格式
- `test_mineru.py` / `test_paper_providers.py`：provider 层逻辑（v1/v4 API 流程、各源请求解析）
