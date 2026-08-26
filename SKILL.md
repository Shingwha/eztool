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
| AI-synthesized answer with citations | `eztool search "<q>" --summarize` (or `--all --summarize` for broad coverage) |
| Broad search (all default providers in parallel, merged + deduped) | `eztool search "<q>" --all` |
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
- `--count` defaults are sane (web 20) — don't add it without a reason.

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

1. **Search**: `eztool search "<query>"`; `--all` for broad multi-source coverage.
2. **Read full text**: `eztool fetch <url>` for URLs in results (output is never
   truncated); `eztool convert <file> --out out.md` for local files.
3. **Credential errors** (exit 2): guide the user through
   `eztool config set providers.<name>.api_key`, verify with `eztool config test`.
4. **Failures**: exit 1 = operational (no results / API error), exit 2 = usage or
   credentials; stderr format is `error: <reason>` + `code: <semantic code>`.

## Resources

- `references/guide.md` — configuration & troubleshooting: read when setting
  credentials, changing chains/timeouts, or decoding errors.
