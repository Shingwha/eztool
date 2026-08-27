---
name: eztool
description: >-
  Unified CLI for web search, reading webpage content and document
  conversion. Use whenever the user asks to search the web (联网搜索 / 豆包 /
  火山引擎 / 查最新信息), fetch/read the content of a webpage or article URL
  (获取网页内容 / 读取网页 / 查看文章全文), or convert a local file
  (PDF/DOCX/XLSX/CSV…) to Markdown. One command covers search, reading AND
  conversion — use it even if the user doesn't name a specific backend.
---

# eztool

One CLI: **search** (web) + **fetch** (URL → Markdown) + **convert** (local
file → Markdown) + **config**. Zero dependencies, pure stdlib; the repo is the
skill.

## When to use

| The user wants | Run |
|---|---|
| Web search / fact-check / latest info | `eztool search "<q>"` |
| A wider sweep | `eztool search "<q>" --max N` |
| Cross-check engines in one merged list | `eztool search "<q>" --use doubao,keen [--summarize]` |
| Read a full webpage / article | `eztool fetch <url>` |
| Convert a local file (PDF/DOCX/XLSX/CSV…) to Markdown | `eztool convert <file> [--out out.md]` |
| Configure credentials / fallback chains | `eztool config` (see references/guide.md) |

## Command cheat sheet

```bash
eztool search "<query>" [--use a,b] [--max N] [--out x.md]
                        [--timeout N] [--list-providers] [--summarize]
eztool fetch <url>... [--out x.md] [--use a,b] [--timeout N] [--list-providers]
                      [--summarize [--query "focus"]]
eztool convert <file> [--out x.md] [--use a,b] [--timeout N] [--list-providers]
                      [--summarize [--query "focus"]]
eztool config show|set|get|reset|test|clear
```

- **Execution paths** — omitting `--use` walks the configured `chains.*`
  fallback: serial, first success wins, uncredentialed-but-required providers
  skipped silently. Naming providers changes things: one = run it alone;
  multiple = **search** runs them in parallel and merges fairly (round-robin,
  URL-deduped, source-tagged, hard-capped at 40) while **fetch,convert** try
  them as a sequential override chain (first success stops). Naming one without
  credentials is an error (exit 2) instead of a silent skip.
- **`--max N`** sweeps the provider list in order until ~N distinct results
  accumulate (the last provider may push slightly past N; the overshoot is
  kept). Without it, every engine returns its own server-side default count.
- **Search timing** — with no timeout setting anywhere (`--timeout`,
  `providers.<name>.timeout`, `settings.timeout` all unset), searches give up
  at 10 s while fetch/convert allow 30 s; any explicitly set value wins
  everywhere.
- **`--out PATH`** redirects output to a file on every command (stdout gets a
  one-line confirmation).
- **Phrase `<query>` as a short question**, not keyword piles:
  `"how do tokio and async-std compare in 2026?"` beats `"tokio async-std"`;
  a question also steers `--summarize`.
- **Date-sensitive queries name absolute dates** — `YYYY-MM-DD` for a day,
  `YYYY-MM-DD..YYYY-MM-DD` for a week/month, `YYYY` for a year. Relative words
  ("today", "this week") mean nothing to engines and pull stale pages.

## Core rules

- **Quality gate** (fetch/convert): short content (<800 chars) hitting
  blocking phrases (captcha / Cloudflare / “环境异常”) in its first 200 chars
  is a bot-check page and falls through automatically; 800–1500 chars with a
  hit stays as backup, returned only if every provider fails (with a warning).
- **`--summarize`** (search/fetch/convert): retrieval first, then an
  OpenAI-compatible LLM synthesizes an answer whose citation links are
  program-generated from the actual result set; the raw output is replaced,
  and LLM failure degrades back to raw (stderr warning). Give fetch/convert a
  concrete `--query "focus"` — it is the difference between a targeted answer
  and a flat abstract (search reuses its own query). Needs
  `summarize.base_url` / `.api_key` / `.model`; missing config exits 2 before
  any retrieval.
- **Credentials** live in `~/.config/eztool/config.json`, settable via
  `eztool config set providers.<name>.api_key`; **never hardcode keys**.
  doubao / parallel require keys; the rest work anonymously and keys raise
  quota (a mineru token upgrades to the v4 API).
- **Exit codes**: 0 success / 1 operational failure (no results, API error) /
  2 usage or missing credentials / 130 Ctrl+C. Stderr lines like
  `[provider] OK (elapsed, size)` form the chain log showing which backend
  served.

## Agent workflow

1. **Start narrow**: one plain `eztool search "<one-sentence question>"`.
   When results feel thin, widen deliberately — reword the query, pin another
   engine with `--use`, sweep wider with `--max N`, or run
   `--use a,b --summarize` for cross-verification. Skip URLs collected in
   earlier rounds.
2. **Go deep**: `eztool fetch <url1> <url2>` (parallel, never truncated) for
   promising links; `eztool convert <file> --out out.md` for local files.
3. **On failure**, read the stderr chain log first. Exit 1 → consider retrying
   via `--use` / a raised `--timeout`. Exit 2 → walk the user through
   `eztool config set providers.<name>.api_key` and verify with
   `eztool config test`.

## Resources

- `references/guide.md` — configuration & troubleshooting: read when setting
  credentials, changing chains/timeouts, or decoding errors.
