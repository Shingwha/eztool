# eztool user guide — configuration & troubleshooting

> Day-to-day usage: see SKILL.md. Come here when you need to configure
> credentials, change fallback chains, tune timeouts, or decode an error.

## Where config lives

All config lives in `~/.config/eztool/config.json` (the `EZTOOL_CONFIG_DIR`
environment variable overrides the directory; `eztool config show` prints the
actual path on its first line). A corrupted config file silently falls back to
defaults — delete it with `eztool config clear` if in doubt.

| Command | Effect |
|---|---|
| `eztool config set <key> [value]` | Set a key (prompts interactively if the value is omitted; secrets are hidden) |
| `eztool config get <key>` | Read one key (secrets masked) |
| `eztool config show` | All config values + config file path |
| `eztool config reset <key>` | Reset a key to its default |
| `eztool config test [--providers a,b]` | Verify configured credentials with real requests |
| `eztool config clear` | Delete the whole config file |

## Three-section structure

```jsonc
{
  "settings":  { "timeout": 30 },                        // global default timeout
  "chains":    { "web": [...], "image": [...], "data": [...], "page": [...], "file": [...] },
  "providers": { "<name>": { "api_key": ..., "timeout": ..., ... } }
}
```

- **settings**: global defaults (currently just `timeout`).
- **chains**: the five fallback chains, one per category (`web`/`image`/`data`
  for search, `page` for fetch, `file` for convert). Values are comma-separated
  provider names tried in order, first success wins.
- **providers**: per-provider credentials and private settings. Everything
  `config show` lists is a valid key — unknown keys are rejected.

**Timeout precedence**: `--timeout` (CLI flag) > `providers.<name>.timeout` >
`settings.timeout`.

The only environment override besides `EZTOOL_CONFIG_DIR`:
`DEEPSEEK_WS_BASE_URL` (DeepSeek API base URL).

## Credentials: who needs what

Providers with **required** credentials are auto-skipped by default chains when
unconfigured; naming one explicitly with `--use` errors instead (exit 2).

| Provider | How to get the key |
|---|---|
| `doubao` (required) | Set `providers.doubao.api_key` (Doubao WebSearch API key), or `providers.doubao.ak` + `providers.doubao.sk` (Volcengine AccessKey/SecretKey) — either one. |
| `deepseek` (required) | API key from https://platform.deepseek.com → `providers.deepseek.api_key`. |
| `exa` (required) | API key from https://dashboard.exa.ai → `providers.exa.api_key`. Not in any default chain; use `--use exa` or add it to `chains.web`/`chains.page`. |
| `parallel` (required) | API key from https://platform.parallel.ai → `providers.parallel.api_key`. Not in any default chain; use `--use parallel` or add it to `chains.web`/`chains.page`. |

These work **anonymously** (rate-limited) — no key needed; setting one raises
quota:

| Provider | Optional key |
|---|---|
| `anysearch` | `providers.anysearch.api_key` |
| `tavily` | `providers.tavily.api_key` (unset = keyless free mode) |
| `keen` | `providers.keen.api_key` (unset = keyless public pool, 1000 req/h per IP) |
| `jina_reader` | `providers.jina_reader.api_key` |
| `firecrawl` | `providers.firecrawl.api_key` |
| `mineru` | `providers.mineru.api_key` — a token upgrades to the v4 Precision API (≤200MB/200 pages/batch/HTML); unset = v1 lightweight API (≤10MB/20 pages) |
| `markdown_new`, `anydoc` | No key exists (free service / local library) |

```bash
eztool config set providers.doubao.api_key    # interactive prompt, hidden input
eztool config test                            # verify everything you configured
```

## Full key table

| Key | Default | Notes |
|---|---|---|
| `settings.timeout` | 30 | Global default timeout in seconds |
| `chains.web` | `doubao,anysearch,deepseek,keen` | Web search fallback chain |
| `chains.image` | `doubao` | Image search fallback chain |
| `chains.data` | `anysearch` | Data source fallback chain |
| `chains.page` | `markdown_new,jina_reader,anysearch,tavily,firecrawl,keen` | URL fetch fallback chain |
| `chains.file` | `anydoc,markdown_new,mineru` | Local file parsing fallback chain |
| `providers.doubao.api_key` / `ak` / `sk` | — (secret) | Doubao credentials: api_key (Bearer) or Volcengine AK+SK |
| `providers.doubao.auth` | auto | Auth method: `apikey` / `aksk` (empty = auto-detect) |
| `providers.doubao.count_web` | 20 | Web result count (1–50) |
| `providers.doubao.count_image` | 5 | Image result count (1–5) |
| `providers.doubao.need_url` / `need_content` | false | Only return results with landing URLs / with full content |
| `providers.doubao.content_formats` | — | Content format: `text` / `markdown` |
| `providers.doubao.time_range` | — | `OneDay/OneWeek/OneMonth/OneYear` or `YYYY-MM-DD..YYYY-MM-DD` |
| `providers.doubao.industry` | — | Industry search: `finance` / `game` / `gov` |
| `providers.anysearch.api_key` | — (secret) | Optional; raises quota |
| `providers.anysearch.max_results` | 20 | Number of results (1–20) |
| `providers.deepseek.api_key` | — (secret) | Required |
| `providers.deepseek.model` | deepseek-v4-flash | `deepseek-v4-flash` / `deepseek-v4-pro` |
| `providers.deepseek.thinking` | enabled | `enabled` / `disabled` (enabled: more accurate, slower, costlier) |
| `providers.deepseek.max_tokens` | 32768 | Max output tokens |
| `providers.tavily.api_key` | — (secret) | Optional; unset = keyless free mode |
| `providers.keen.api_key` | — (secret) | Optional; unset = keyless public pool |
| `providers.parallel.api_key` | — (secret) | Required |
| `providers.exa.api_key` | — (secret) | Required |
| `providers.jina_reader.api_key` | — (secret) | Optional; raises quota |
| `providers.firecrawl.api_key` | — (secret) | Optional; raises quota |
| `providers.mineru.api_key` | — (secret) | Optional; set = v4 Precision API, unset = v1 lightweight API |
| `providers.<name>.timeout` | varies | Per-provider timeout: tavily/exa/markdown_new 30, jina_reader 10, firecrawl/anydoc 60, mineru 300, everything else 30 |

## Changing fallback chains

```bash
eztool config show                              # current chains
eztool config set chains.web "deepseek,doubao"  # comma-separated, tried in order
eztool config reset chains.web                  # back to the derived default
```

Rules of thumb:

- Order matters: first success wins. Cheaper/faster providers go first.
- Providers needing credentials you haven't set are skipped silently in chains
  (but error when named via `--use`).
- Some providers are deliberately **not** in the defaults: `exa` / `parallel`
  (paid, key required), `tavily` for web search, `mineru` for URL fetch. Add
  them to a chain or name them with `--use`.
- One-off overrides don't need config changes: `eztool search "q" --use exa`,
  `eztool fetch <url> --use jina_reader,firecrawl`.

## Reading errors and exit codes

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | Success | — |
| 1 | Operational failure (no results, API error, all providers failed) | Read the stderr chain log; try `--use` to pin a provider or raise `--timeout` |
| 2 | Usage error or missing credentials | Fix the arguments, or set the key the message names |
| 130 | Ctrl+C | — |

Errors print to stderr as:

```
error: <reason>
code: <semantic code>     # e.g. missing_credentials, search_failed, convert_failed
```

While a chain runs, stderr shows its progress — use it to see which backend
actually served and why others didn't:

```
[doubao] failed: HTTP 401 (0.4s) -> next provider
[anysearch] OK (1.2s, 20 results)
```

## Troubleshooting

**"provider 'X' requires credentials" (exit 2)**
You named X with `--use` but haven't configured its key. Run
`eztool config set providers.X.api_key` (for doubao: `api_key`, or `ak` + `sk`),
then verify with `eztool config test --providers X`. Never paste keys into
commands or files outside the config.

**`--image` search says all providers failed**
The image chain is doubao-only and doubao needs credentials. Configure them
(see above) — without them there is no image search.

**"content looks suspicious … it may be incomplete"**
Every provider hit what looks like a bot-check/interstitial page, and the least
bad result was kept as backup. Retry later, pick another provider with `--use`,
or configure a credential that raises quota. Pages that clearly are bot checks
(short, hitting phrases like captcha/"环境异常") are never returned silently —
the chain falls through automatically.

**`fetch` returns a login/verification wall for one site**
Try a different rendering path: `--use firecrawl` (browser rendering) or
`--use mineru` for document URLs. If the site hard-blocks datacenter IPs, no
provider will get through.

**Timeouts / a provider hangs**
Raise the budget for one run with `--timeout 60`, or permanently per provider:
`eztool config set providers.<name>.timeout 60`. `jina_reader` defaults to just
10s and `mineru` to 300s (async polling) — adjust those first.

**`config set` says "unknown config key"**
The key isn't declared by any provider. Run `eztool config show` for the full
list of valid keys — it is authoritative.

**`convert` fails on an exotic / scanned file**
`anydoc` (local) fails fast and the chain falls back to the cloud; scanned
PDFs need OCR, which only `mineru` provides. Check `eztool convert <file>
--use mineru`, and make sure the `[local]` extra is installed if you want the
fast local path (`uv tool install ".[local]"`).

**Nothing works at all**
Check connectivity to the providers' endpoints (some, e.g. jina.ai, are
unreachable from certain networks — the chain times out and moves on). Then
`eztool config test` to smoke-test every configured credential.
