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
eztool config set providers.doubao.api_key     # 豆包/火山 WebSearch 凭证（或 ak+sk）
eztool config set providers.deepseek.api_key   # DeepSeek key（可选）
eztool config set providers.anysearch.api_key  # AnySearch key（可选，匿名可用）
eztool config test                   # 验证凭证
```

配置存于 `~/.config/ezwork-tool/config.json`（`eztool config path` 查看）。完整配置项见 `SKILL.md`。

## 后端

| 后端 | 特点 | 凭证 |
|---|---|---|
| `doubao` | 网页+图片搜索、域名/时间/行业过滤、权威过滤 | 需要（API Key 或 AK/SK） |
| `anysearch` | 40+ 数据源标签（学术/代码/金融/安全/法律/旅行…） | 可选，匿名可用 |
| `deepseek` | 服务端搜索 + AI 合成回答 + 来源列表 | 需要（DeepSeek API Key） |
| `openalex` | 学术论文：250M+ 全学科（期刊+预印本）、年份/作者/OA 过滤、引用排序 | 无 |
| `arxiv` | 预印本（CS/物理/数学/生/经），快而干净 | 无 |
| `crossref` | 期刊 DOI 元数据（含引用数），兜底 | 无 |

- `--backend auto`（默认）：按 search.providers 链逐个尝试，失败自动换下一个（failover）。
- `--backend a,b`（逗号分隔）：**多后端并行汇总**——同时搜、去重合并（按 DOI→URL→标题归一化，first wins），每条结果标注来源。
- `eztool paper "query"`：论文专用命令，默认并行汇总 openalex+arxiv+crossref 三源，输出论文卡片（作者/年份/期刊/引用数/DOI/OA 链接），支持 `--backend`（auto / 逗号列表 / 单个源）、`--year`（如 `2023` 或 `2020-2024`）、`--author`、`--sort relevance|cited|date`、`--oa`、`--count`。

## 开发

```bash
PYTHONPATH=src python -m ezwork_tool.cli --help   # 免安装运行
uv run pytest tests/ -q                            # 测试（如配置了 pytest）
```

MIT License
