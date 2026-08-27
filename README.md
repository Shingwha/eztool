# eztool

Unified CLI: **search** (`eztool search` — web search across 5 providers:
Tavily, Doubao, AnySearch, Keen, Parallel) +
**fetch** (URL → Markdown) + **convert** (local file → Markdown) + **config**.
One tool, one skill (`SKILL.md` is the skill; the repo is the skill).
Zero dependencies, pure Python stdlib.

```bash
eztool search "Rust async 2026"           # web search (tavily→doubao→anysearch→keen→parallel fallback chain)
eztool search "LLM agents" --max 15       # sweep the chain until ~15 distinct results
eztool search "LLM agents" --use keen,tavily --summarize  # two engines, one cited synthesis
eztool fetch https://example.com/article  # URL → Markdown (markdown_new→tavily→jina_reader→firecrawl→keen→parallel)
eztool fetch https://a/ https://b/ --summarize --query "pricing"  # multi-URL fetch + AI synthesis
eztool convert report.pdf --out report.md # local file → Markdown (anydoc→markdown_new→mineru)
eztool config test                        # verify credentials
```

## Docs

| Doc | Contents |
|---|---|
| [`SKILL.md`](SKILL.md) | Core usage guide (when to use / command cheat sheet / workflow) |
| [`references/guide.md`](references/guide.md) | User guide: configuration, credentials, chains, timeouts, exit codes, troubleshooting |
| [`script/`](script/) | All code (pyproject + src + tests) |

## Install

```bash
cd script
uv tool install .              # base install
uv tool install -e .           # editable install for development
uv tool install ".[local]"     # optional: local document parsing (firecrawl-anydoc, 14 formats)
eztool --help
```

## Quick config

```bash
eztool config set providers.doubao.api_key    # Doubao/Volcengine WebSearch (or ak+sk)
eztool config set providers.tavily.api_key    # Tavily (optional, keyless works rate-limited)
eztool config set providers.anysearch.api_key # AnySearch (optional, works anonymously)
eztool config test                            # verify credentials
```

Config lives at `~/.config/eztool/config.json` — full key table, chain tuning
and troubleshooting: [`references/guide.md`](references/guide.md).

## Development

Architecture: `util` (errors/HTTP/quality gate) → `provider` (base class +
registry) → `providers/` (implementations) → `api` (routing + chains) →
`summarize` (LLM synthesis) → `format` → `cli`.

```
script/src/eztool/
├── util.py       # exception taxonomy + HTTP helpers + content quality gate
├── provider.py   # Provider base class + metadata declarations + registry (SERVICES)
├── providers/    # 10 provider implementations; __init__.py is the only registration point
├── api.py        # category routing + chain/parallel execution + quality gate + summarize hooks
├── summarize.py  # --summarize: summarizer registry + OpenAI-compatible backend + prompt/citations
├── format.py     # output formatting (Markdown)
├── config.py     # config I/O; DEFAULTS/SECRET_KEYS/KEY_HINTS generated from metadata
└── cli.py        # argparse command surface + dispatch
```

**Adding a provider, two steps**: ① write `providers/foo.py` (a class declaring
`name`/`categories`/`config`/`priority`/`auth_required` plus
the capability methods); ② add one import line to `providers/__init__.py`.
Config keys, default chains, `config show` and `--list-providers`
all appear automatically — a new config key touches
only the provider file.

```bash
cd script
uv run python -m eztool.cli --help   # run without installing
uv run --group dev pytest -q         # tests (127 cases, fully mocked, zero network)
uv tool install ".[local]" --force --reinstall   # reinstall after changes
```

Tests live in `script/tests/` (registry / chains / quality gate / config / CLI /
format / per-provider protocol details); `conftest.py` provides fakes and mocks
so nothing touches the network.

MIT License
