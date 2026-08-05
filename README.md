# ezwork-tool

统一 CLI：**搜索**（`search web` / `search image` / `search paper` / `search data`，覆盖 Doubao / AnySearch / DeepSeek / OpenAlex / arXiv / Crossref 11 个 provider）+ **转换**（`convert`，URL 或本地文件 → Markdown）+ **配置**（`config`）。
一个工具、一个 skill（`SKILL.md` 即 skill，repo 即 skill）。零依赖、纯 Python 标准库。

替代：`doubao-websearch` / `anysearch` / `deepseek-ws` / `ezwork-fetch` 四个独立 CLI。

```bash
eztool search web "Rust async 2026"              # 通用搜索（doubao→anysearch→deepseek 回退链）
eztool search image "猫" --width-min 800          # 图片搜索（直链 + 尺寸/形状元数据）
eztool search paper "vision transformer"         # 论文搜索：openalex+arxiv+crossref 三源并行汇总
eztool search paper "LLM reasoning" --year 2024 --sort cited --oa
eztool search data "AAPL" --tag finance.quote    # 专业数据源（anysearch）
eztool search tags                               # 数据源标签目录（40+）
eztool convert https://example.com/article       # URL → Markdown（markdown_new→jina_reader→anysearch→firecrawl）
eztool convert report.pdf --out report.md        # 本地文件 → Markdown（anydoc→markdown_new→mineru）
eztool config test                               # 验证凭证
```

## 文档导航

| 文档 | 内容 |
|---|---|
| [`SKILL.md`](SKILL.md) | 核心使用指引（什么时候用 / 命令速查 / Workflow / 扩展指南） |
| [`references/configuration.md`](references/configuration.md) | 全部配置项、配置命令、限流配额 |
| [`references/backends.md`](references/backends.md) | provider 类别声明总表、参数归属、论文搜索细节 |
| [`references/development.md`](references/development.md) | 安装 / 测试 / 更新 / 架构 |
| [`script/`](script/) | 全部代码（pyproject + src + tests） |

## 安装

```bash
cd script && uv tool install .                     # 基础安装
uv tool install ".[local]"                          # 可选：本地文档解析（firecrawl-anydoc，14 格式）
eztool --help
```

## 快速配置

```bash
eztool config set providers.doubao.api_key     # 豆包/火山 WebSearch 凭证（或 ak+sk）
eztool config set providers.deepseek.api_key   # DeepSeek key（可选）
eztool config set providers.anysearch.api_key  # AnySearch key（可选，匿名可用）
eztool config test                   # 验证凭证
```

配置存于 `~/.config/ezwork-tool/config.json`（`eztool config show` 首行显示路径）。

## 开发

```bash
cd script
PYTHONPATH=src python -m ezwork_tool.cli --help   # 免安装运行
PYTHONPATH=src python -m unittest discover tests -q   # 测试
```

MIT License
