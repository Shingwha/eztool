# ezwork-tool

统一 CLI：**搜索**（Doubao / AnySearch / DeepSeek / OpenAlex / arXiv / Crossref 六个后端）+ **论文搜索**（`paper` 多源汇总）+ **URL 抓取转 Markdown** + **本地文件转 Markdown**。
一个工具、一个 skill（`SKILL.md` 即 skill，repo 即 skill）。零依赖、纯 Python 标准库。

替代：`doubao-websearch` / `anysearch` / `deepseek-ws` / `ezwork-fetch` 四个独立 CLI。

```bash
eztool search "Rust async 2026"                  # auto 逐个回退后端
eztool search "AAPL" --tag finance.quote         # anysearch 数据源
eztool search "猫" --image                       # doubao 图片搜索
eztool search "q" --backend deepseek             # DeepSeek AI 合成回答
eztool search "q" --backend openalex,arxiv       # 多后端并行汇总（去重合并）
eztool paper "vision transformer"                # 论文搜索：openalex+arxiv+crossref 三源汇总
eztool paper "LLM reasoning" --year 2024 --sort cited --oa
eztool fetch https://example.com/article         # URL → Markdown
eztool convert report.pdf                        # 本地文件 → Markdown（markdown.new→MinerU 回退链）
eztool convert 报告.docx --out report.md          # 写入文件而非 stdout
eztool tags                                      # 数据源标签目录
```

## 文档导航

| 文档 | 内容 |
|---|---|
| [`SKILL.md`](SKILL.md) | 核心使用指引（什么时候用 / 快速上手 / Workflow） |
| [`references/configuration.md`](references/configuration.md) | 全部配置项、配置命令、限流配额 |
| [`references/backends.md`](references/backends.md) | 后端能力矩阵、参数归属、论文搜索细节 |
| [`references/development.md`](references/development.md) | 安装 / 测试 / 更新 / 架构 |
| [`script/`](script/) | 全部代码（pyproject + src + tests） |

## 安装

```bash
cd script && uv tool install .
eztool --help
```

## 快速配置

```bash
eztool config set providers.doubao.api_key     # 豆包/火山 WebSearch 凭证（或 ak+sk）
eztool config set providers.deepseek.api_key   # DeepSeek key（可选）
eztool config set providers.anysearch.api_key  # AnySearch key（可选，匿名可用）
eztool config test                   # 验证凭证
```

配置存于 `~/.config/ezwork-tool/config.json`（`eztool config path` 查看）。

## 开发

```bash
cd script
PYTHONPATH=src python -m ezwork_tool.cli --help   # 免安装运行
PYTHONPATH=src python -m unittest discover tests -q   # 测试
```

MIT License
