---
name: eztool
description: >-
  Unified CLI for search and document conversion: `eztool search "<q>"`
  (web by default; `--image` for images; `--source <tag>` for 40 specialized
  data sources; Doubao / AnySearch / DeepSeek / Tavily / Exa / Keen /
  Parallel), `eztool fetch
  <url>` and `eztool convert <file>` (URL or local file to Markdown), and
  `eztool config`. Use whenever the user asks to search the web (联网搜索 /
  豆包 / 火山引擎 / DeepSeek 搜索 / 查最新信息), search images, search
  specialized data sources (code, finance quotes, security CVEs, legal,
  travel, news…), fetch/read the content of a webpage or article URL, or
  convert a local file (PDF/DOCX/XLSX/image/CSV…) to Markdown. One command
  (eztool) covers search AND conversion — use it even if the user doesn't
  name a specific backend.
---

# eztool

One command for search → fetch → convert → config: **search** (web / image / data
sources — category is an option, not a subcommand) + **fetch** (URL → Markdown) +
**convert** (local file → Markdown) + **config**. Zero dependencies, pure stdlib;
the repo is the skill.

## When to use

| The user wants | Run |
|---|---|
| Web search / AI-synthesized answer (news, versions, prices, fact-check) | `eztool search "<q>"` |
| Image search (direct links + size/shape metadata) | `eztool search "<q>" --image` |
| Specialized data: quotes / code / CVEs / legal / travel | `eztool search "<q>" --source <tag>` (run `eztool sources` first) |
| Broad search (all default providers in parallel, merged + deduped) | `eztool search "<q>" --all` |
| Read a full webpage / article | `eztool fetch <url>` |
| Convert a local file (PDF/DOCX/XLSX/CSV…) to Markdown | `eztool convert <file> [--out out.md]` |
| Configure credentials / fallback chains | `eztool config` (see references/guide.md) |

## Command cheat sheet

```bash
eztool search "<query>" [--image | --source TAG [--params '{...}']]
                        [--all | --use a,b] [--count N] [--timeout N] [--list-providers]
                        [--summarize]
eztool sources                       # data source tag catalog (for --source)
eztool fetch <url>... [--out x.md] [--use a,b] [--timeout N] [--list-providers]
                      [--summarize [--query "focus"]]
eztool convert <file> [--out x.md] [--use a,b] [--timeout N] [--list-providers]
                      [--summarize [--query "focus"]]
eztool config show|set|get|reset|test|clear
```

- `--use a,b`: one = run it alone; multiple = **search: parallel merge** (URL-dedup,
  `**[source]**` tagged) / **fetch,convert: sequential override chain** (try in order,
  stop at first success). Omit → the configured `chains.*` fallback chain.
- Named providers are never credential-skipped — naming one without credentials is
  an error (exit 2).
- Image-only filters (doubao): `--width-min/--width-max/--height-min/--height-max`,
  `--shapes`. `--count` defaults are sane (web/data 20, image 5) — don't add it
  without a reason.

## Core rules

- **Category routing**: search category (web/image/data) comes from `--image` /
  `--source`; fetch = page chain, convert = file chain. Params and backends can't
  mismatch by construction (`--image` only ever reaches doubao).
- **Fallback chains (default path)**: serial, first success wins; providers with
  `auth_required` but no credentials are auto-skipped, anonymous ones always run.
  Defaults derive from each provider's `priority`; override with
  `eztool config set chains.web "a,b"`.
- **Quality gate** (fetch/convert): content whose first 200 chars hit blocking
  phrases ("环境异常"/captcha/Cloudflare…) and is <800 chars = bot-check page →
  treat as failure and fall through; 800–1500 = suspicious → kept as backup
  (returned with a stderr warning only if everything else fails). WeChat
  verification pages therefore fall back automatically — never return them.
- **`--summarize`** (search/fetch/convert): after retrieval, an OpenAI-compatible
  LLM synthesizes an answer with citations — output is the answer + a Sources
  list (`[n] title — url **[provider]**`, program-generated, never LLM-written
  links). Raw results are replaced; LLM failure degrades back to raw output
  (stderr warning). fetch takes multiple URLs (parallel); for fetch/convert
  **always add `--query "focus"`** — a concrete question beats the generic
  summary fallback by a wide margin (search uses its own query as the request).
  Requires explicit `summarize.base_url` / `summarize.api_key`
  / `summarize.model` — missing config = exit 2 before any retrieval.
- **Credentials**: doubao / deepseek / exa / parallel require keys (doubao:
  api_key or ak+sk); anysearch / tavily / keen / firecrawl / jina_reader /
  markdown_new / mineru / anydoc work anonymously (keys raise quota; a mineru
  token upgrades to the v4 API).
  On credential errors tell the user `eztool config set providers.<name>.api_key`;
  **never hardcode keys**. Config lives at `~/.config/eztool/config.json`.
- **Exit codes**: 0 success / 1 operational failure (incl. no results) / 2 usage
  or missing credentials. stderr `[provider] OK (elapsed, size)` lines are the
  chain log — check them to see which backend actually served.

## Agent workflow

1. **Search**: `eztool search "<query>"`; add `--image` for images, `--source <tag>`
   for specialized data (`eztool sources` for the tag list), `--all` for broad
   multi-source coverage.
2. **Read full text**: `eztool fetch <url>` for URLs in results (output is never
   truncated); `eztool convert <file> --out out.md` for local files.
3. **Credential errors** (exit 2): guide the user through
   `eztool config set providers.<name>.api_key`, verify with `eztool config test`.
4. **Failures**: exit 1 = operational (no results / API error), exit 2 = usage or
   credentials; stderr format is `error: <reason>` + `code: <semantic code>`.

## Resources

- `references/guide.md` — configuration & troubleshooting: read when setting
  credentials, changing chains/timeouts, or decoding errors.
