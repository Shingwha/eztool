# eztool

Unified CLI: **search** (`eztool search` — web / image / 40 specialized data
sources across 12 providers: Doubao, AnySearch, DeepSeek, Tavily, Exa, Keen,
Parallel…) +
**fetch** (URL → Markdown) + **convert** (local file → Markdown) + **config**.
One tool, one skill (`SKILL.md` is the skill; the repo is the skill).
Zero dependencies, pure Python stdlib.

Replaces four standalone CLIs: `doubao-websearch` / `anysearch` / `deepseek-ws` /
`ezwork-fetch`.

```bash
eztool search "Rust async 2026"           # web search (doubao→anysearch→deepseek→keen fallback chain)
eztool search "cats" --image --width-min 800   # image search (direct links + size/shape metadata)
eztool search "AAPL" --source finance.quote --params '{"type":"quote"}'  # data source (anysearch, 40 tags)
eztool search "LLM agents" --all          # whole default chain in parallel + merge/dedup
eztool sources                            # data source tag catalog
eztool fetch https://example.com/article  # URL → Markdown (markdown_new→jina_reader→anysearch→tavily→firecrawl→keen)
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
eztool config set providers.deepseek.api_key  # DeepSeek key (optional)
eztool config set providers.anysearch.api_key # AnySearch key (optional, works anonymously)
eztool config test                            # verify credentials
```

Config lives at `~/.config/eztool/config.json` — full key table, chain tuning
and troubleshooting: [`references/guide.md`](references/guide.md).

## Development

Architecture: `util` (errors/HTTP/quality gate) → `provider` (base class +
registry) → `providers/` (implementations) → `api` (routing + chains) →
`format` → `cli`.

```
script/src/eztool/
├── util.py       # exception taxonomy + HTTP helpers + content quality gate
├── provider.py   # Provider base class + metadata declarations + registry (SERVICES)
├── providers/    # 12 provider implementations; __init__.py is the only registration point
├── api.py        # category routing + chain/parallel execution + quality gate
├── format.py     # output formatting (Markdown)
├── config.py     # config I/O; DEFAULTS/SECRET_KEYS/KEY_HINTS generated from metadata
└── cli.py        # argparse command surface + dispatch
```

**Adding a provider, two steps**: ① write `providers/foo.py` (a class declaring
`name`/`categories`/`config`/`params`/`priority`/`auth_required`/`sources` plus
the capability methods); ② add one import line to `providers/__init__.py`.
Config keys, CLI params, default chains, `config show`, `sources` and
`--list-providers` all appear automatically — a new config key or param touches
only the provider file.

```bash
cd script
uv run python -m eztool.cli --help   # run without installing
uv run --group dev pytest -q         # tests (121 cases, fully mocked, zero network)
uv tool install ".[local]" --force --reinstall   # reinstall after changes
```

Tests live in `script/tests/` (registry / chains / quality gate / config / CLI /
format / per-provider protocol details); `conftest.py` provides fakes and mocks
so nothing touches the network.

MIT License
