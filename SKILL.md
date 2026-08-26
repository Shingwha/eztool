---
name: eztool
description: >-
  Unified CLI for web search and document conversion: `eztool search "<q>"`
  (web search via Tavily / Doubao / AnySearch / Keen / Parallel),
  `eztool fetch <url>` and `eztool convert <file>` (URL or local file to
  Markdown), and `eztool config`. Use whenever the user asks to search the
  web (联网搜索 / 豆包 / 火山引擎 / 查最新信息), read the full content of a
  webpage or article URL, or convert a local file (PDF/DOCX/XLSX/CSV…) to
  Markdown. One command (eztool) covers search AND conversion — use it even
  if the user doesn't name a specific backend.
---

# eztool

One command for search → fetch → convert → config: **search** (web) +
**fetch** (URL → Markdown) + **convert** (local file → Markdown) + **config**.
Zero dependencies, pure stdlib; the repo is the skill.

## When to use

| The user wants | Run |
|---|---|
| Web search / fact-check / latest info | `eztool search "<q>"` |
| Broad search (all default providers in parallel, merged + deduped) | `eztool search "<q>" --all` |
| One synthesized answer across multiple sources | `eztool search "<q>" --all --summarize` — multi-provider results fed to the LLM and written up with citations; **prefer this whenever `--all` or a multi-provider `--use` is involved** (needs `summarize.*` config — see references/guide.md) |
| Read a full webpage / article | `eztool fetch <url>` |
| Convert a local file (PDF/DOCX/XLSX/CSV…) to Markdown | `eztool convert <file> [--out out.md]` |
| Configure credentials / fallback chains | `eztool config` (see references/guide.md) |

## Command cheat sheet

```bash
eztool search "<query>" [--all | --use a,b] [--count N] [--timeout N]
                        [--list-providers] [--summarize]
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
- **Write `<query>` as a short question, not keyword piles.** The providers are
  natural-language friendly and a question beats bare keywords in practice
  (e.g. `"how do tokio and async-std compare in 2026?"` finds decision-oriented
  results, while `"tokio async-std"` drifts toward tutorials). Include the key
  terms inside the question. With `--summarize`, the query doubles as the
  synthesis request — a question steers it, keywords don't.
- `--count N` is an explicit override: omit it and each provider uses its
  **server-side default** (tavily 5, parallel 10, doubao/anysearch/keen server-set).
  Don't add `--count` without a reason.

## Core rules

- **Fallback chains (default path)**: serial, first success wins; providers with
  `auth_required` but no credentials are auto-skipped, anonymous ones always run.
  Defaults derive from each provider's `priority`; override with
  `eztool config set chains.web "a,b"`.
  Stale names in configured chains (e.g. after removing a provider) are warned
  about on stderr and dropped — never fatal.
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
  **Multi-provider searches prefer `--summarize`**: the point of `--all` /
  multi-provider `--use` is cross-source coverage, and the LLM synthesis is
  what turns overlapping raw results into one deduplicated, cited answer — so
  use `eztool search "<q>" --all --summarize` (or `--use a,b --summarize`)
  whenever summaries are configured; a plain single-provider search without
  `--summarize` is the lightweight default.
  Requires explicit `summarize.base_url` / `summarize.api_key`
  / `summarize.model` — missing config = exit 2 before any retrieval.
- **Credentials**: doubao / parallel require keys (doubao: api_key or ak+sk);
  anysearch / tavily / keen / firecrawl / jina_reader / markdown_new / mineru /
  anydoc work anonymously (keys raise quota; a mineru token upgrades to the
  v4 API).
  On credential errors tell the user `eztool config set providers.<name>.api_key`;
  **never hardcode keys**. Config lives at `~/.config/eztool/config.json`.
- **Exit codes**: 0 success / 1 operational failure (incl. no results) / 2 usage
  or missing credentials. stderr `[provider] OK (elapsed, size)` lines are the
  chain log — check them to see which backend actually served.

## Agent workflow

1. **Search**: `eztool search "<one-sentence question>"` (ask what you want to
   know — a question query beats keyword lists); for broad coverage add `--all`, and
   pair it with `--summarize` so the merged results come back as one cited
   answer rather than raw lists (needs `summarize.*` config).
2. **Read full text**: `eztool fetch <url>` for URLs in results (output is never
   truncated); `eztool convert <file> --out out.md` for local files.
3. **Credential errors** (exit 2): guide the user through
   `eztool config set providers.<name>.api_key`, verify with `eztool config test`.
4. **Failures**: exit 1 = operational (no results / API error), exit 2 = usage or
   credentials; stderr format is `error: <reason>` + `code: <semantic code>`.

## Resources

- `references/guide.md` — configuration & troubleshooting: read when setting
  credentials, changing chains/timeouts, or decoding errors.
