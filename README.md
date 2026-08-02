# ezwork-tool

统一 CLI：**搜索**（Doubao / AnySearch / DeepSeek 三个后端）+ **URL 抓取转 Markdown** + **本地文件转 Markdown**。
一个工具、一个 skill（`SKILL.md` 即 skill，repo 即 skill）。零依赖、纯 Python 标准库。

替代：`doubao-websearch` / `anysearch` / `deepseek-ws` / `ezwork-fetch` 四个独立 CLI。

```bash
eztool search "Rust async 2026"                  # 自动路由后端
eztool search "AAPL" --tag finance.quote         # anysearch 数据源
eztool search "猫" --image                       # doubao 图片搜索
eztool search "q" --backend deepseek             # DeepSeek AI 合成回答
eztool fetch https://example.com/article         # URL → Markdown
eztool convert report.pdf                        # 本地文件 → Markdown（markdown.new→MinerU 回退链）
eztool convert 报告.docx --out report.md          # 写入文件而非 stdout
eztool convert 演示.pptx                          # PPTX：markdown.new 不支持，自动走 MinerU
eztool tags                                      # 数据源标签目录
```

## 安装

```bash
uv tool install .
eztool --help
```

## 配置

```bash
eztool config set doubao.api_key     # 豆包/火山 WebSearch 凭证（或 ak+sk）
eztool config set deepseek.api_key   # DeepSeek key（可选）
eztool config set anysearch.api_key  # AnySearch key（可选，匿名可用）
eztool config test                   # 验证凭证
```

配置存于 `~/.config/ezwork-tool/config.json`（`eztool config path` 查看）。完整配置项见 `SKILL.md`。

## 后端

| 后端 | 特点 | 凭证 |
|---|---|---|
| `doubao` | 网页+图片搜索、域名/时间/行业过滤、权威过滤 | 需要（API Key 或 AK/SK） |
| `anysearch` | 40+ 数据源标签（学术/代码/金融/安全/法律/旅行…） | 可选，匿名可用 |
| `deepseek` | 服务端搜索 + AI 合成回答 + 来源列表 | 需要（DeepSeek API Key） |

`--backend auto`（默认）：doubao → deepseek → anysearch 兜底。输出格式三后端统一。

## 开发

```bash
PYTHONPATH=src python -m ezwork_tool.cli --help   # 免安装运行
uv run pytest tests/ -q                            # 测试（如配置了 pytest）
```

MIT License
