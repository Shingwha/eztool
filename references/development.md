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
    ├── src/ezwork_tool/    # 包源码（registry / providers / api / cli …）
    └── tests/              # unittest 测试
```

## 安装

```bash
cd ezwork-tool/script
uv tool install ".[local]"    # [local] 额外装 pdf-inspector（本地 PDF 解析）
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
# 注意：必须带 [local] extra，否则 pdf-inspector 丢失（uv tool install 没有 --extra 参数）
eztool --help                       # 验证新命令
```

## 架构要点

- **统一注册表** `src/ezwork_tool/registry.py`：provider 类声明 `name` + `capabilities`（search/fetch/convert_file）+ `search_params`，`@register` 登记；CLI 参数/`--backend` 候选/归属校验/`--list-providers` 全部自动生成——新增服务商不改公共代码。
- **公共层**：base.py（Provider 基类 + 数据结构）、http.py（网络工具）、chain.py（run_chain failover + run_fanout 并行汇总）、api.py（search/paper/fetch/convert 入口）、config.py（providers.<name> 一段式配置）、formatter.py（唯一 Markdown 输出）。
- **search 后端**在 providers/doubao.py 等（函数实现私有 `_search`，类做适配）。
- **paper 多源汇总**：api.paper → run_fanout（ThreadPoolExecutor 并行）→ `_merge_search`（DOI→URL→标题归一化去重，first wins，source 回填）。
- **两阶段排序**：openalex/crossref 的 sort=cited/date 先按相关性取候选集（count×5，50–200），客户端重排截断。
- 零依赖纯标准库（Python ≥3.10）。
