"""eztool 统一入口：search / sources / fetch / convert / config。

命令面（类别是选项，不是子命令）：

- ``eztool search "<q>"``：通用搜索；``--image`` 图片搜索；``--source <tag>``
  专业数据源（配合 ``--params`` 传标签额外参数）；``--all`` 默认链全员并行。
- ``eztool sources``：数据源标签清单（注册表聚合）。
- ``eztool fetch <url>...``：URL → Markdown（多 URL 并行）；``eztool convert <file>``：本地文件 → Markdown。
- ``--use a,b``：search = 并行合并；fetch/convert = 顺序覆盖链；1 个 = 单跑。
  缺省走 ``chains.*`` 配置回退链。
- ``--summarize``：search/fetch/convert 通用——内容再过一道 OpenAI 兼容
  LLM（``summarize.*`` 配置）做提炼，输出 AI 答案 + 确定性引用表；
  fetch/convert 可加 ``--query`` 指定关注点。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from urllib.parse import urlparse

from . import __version__
from . import api
from . import config as cfgmod
from . import provider as prov
from .format import (
    format_data,
    format_image,
    format_search,
    format_sources,
    format_summary,
)
from .util import EztoolError, ServiceError, UsageError

_SEARCH_FORMATTERS = {
    "web": format_search,
    "image": format_image,
    "data": format_data,
}


def _add_param(parser: argparse.ArgumentParser, pname: str, spec) -> None:
    """按 ParamSpec 声明生成 argparse 参数。"""
    kwargs = {"help": spec.help, "default": None}
    if spec.action == "store_true":
        kwargs["action"] = "store_true"
    else:
        kwargs["type"] = spec.type
        if spec.choices:
            kwargs["choices"] = spec.choices
        if spec.metavar:
            kwargs["metavar"] = spec.metavar
    parser.add_argument("--" + pname.replace("_", "-"), **kwargs)


def _add_use_timeout(p: argparse.ArgumentParser, convert_mode: bool = False) -> None:
    semantics = ("sequential override chain (try in order, fall through on failure)"
                 if convert_mode else "parallel merge (dedup, source-tagged)")
    p.add_argument("--use", metavar="A,B",
                   help=f"pick providers: one = run it alone; multiple = {semantics}; "
                        f"omit to use the configured chains fallback")
    p.add_argument("--timeout", type=int, default=None,
                   help="timeout in seconds (overrides providers.<name>.timeout "
                        "and settings.timeout)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eztool",
        description="Search (search/sources) + fetch + convert + config. "
                    "One command for searching, reading and converting.",
    )
    p.add_argument("--version", action="version", version=f"eztool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # ── search：类别 = 选项（默认 web）──
    sp = sub.add_parser("search", help="search: general web by default; --image for images; "
                                       "--source for specialized data sources")
    sp.add_argument("query", nargs="?", help="search query (omit with --list-providers)")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--image", action="store_true",
                      help="image search (direct links + size/shape metadata)")
    mode.add_argument("--source", metavar="TAG",
                      help="specialized data source tag (see eztool sources)")
    sp.add_argument("--params", metavar="JSON", default=None,
                    help="with --source: extra tag params, e.g. '{\"type\":\"quote\"}'")
    breadth = sp.add_mutually_exclusive_group()
    breadth.add_argument("--all", action="store_true",
                         help="run the whole default chain in parallel + merge/dedup "
                              "(broad search)")
    breadth.add_argument("--use", metavar="A,B",
                         help="pick providers: one = run it alone; multiple = parallel merge; "
                              "omit to use the configured chains fallback")
    sp.add_argument("--summarize", action="store_true",
                    help="AI-synthesize the results into an answer with citations "
                         "(requires summarize.* config; replaces the raw result list)")
    sp.add_argument("--count", type=int, default=None,
                    help="results per provider (overrides each provider's default)")
    sp.add_argument("--timeout", type=int, default=None,
                    help="timeout in seconds (overrides providers.<name>.timeout "
                         "and settings.timeout)")
    sp.add_argument("--list-providers", action="store_true",
                    help="list providers available for this category and their "
                         "credential status")
    # 类别特有参数（如 doubao 的图片尺寸/形状过滤）由 provider 声明自动并入
    merged_params: dict = {}
    for cat in prov.SEARCH_CATEGORIES:
        merged_params.update(prov.category_params(cat))
    for pname, spec in merged_params.items():
        _add_param(sp, pname, spec)
    sp.set_defaults(func=cmd_search)

    # ── sources：数据源标签清单 ──
    sub.add_parser("sources", help="list all data source tags (use with search --source)"
                   ).set_defaults(func=cmd_sources)

    # ── fetch：URL → Markdown（支持多 URL，并行抓取）──
    fp = sub.add_parser("fetch", help="fetch URL(s) as Markdown (online chain; "
                                      "multiple URLs fetched in parallel)")
    fp.add_argument("target", nargs="+", help="http(s):// URL(s)")
    fp.add_argument("--out", metavar="PATH", help="write to this file instead of stdout")
    fp.add_argument("--summarize", action="store_true",
                    help="AI-synthesize the fetched content into an answer with "
                         "citations (requires summarize.* config)")
    fp.add_argument("--query", metavar="FOCUS",
                    help="with --summarize: the question/focus for the synthesis "
                         "(strongly recommended — beats the generic summary)")
    _add_use_timeout(fp, convert_mode=True)
    fp.add_argument("--list-providers", action="store_true",
                    help="list providers available for the online fetch chain and "
                         "their credential status")
    fp.set_defaults(func=cmd_fetch)

    # ── convert：本地文件 → Markdown ──
    vp = sub.add_parser("convert", help="convert a local file to Markdown (local parsing chain)")
    vp.add_argument("target", nargs="?", help="local file path")
    vp.add_argument("--out", metavar="PATH", help="write to this file instead of stdout")
    vp.add_argument("--summarize", action="store_true",
                    help="AI-synthesize the converted content into an answer with "
                         "citations (requires summarize.* config)")
    vp.add_argument("--query", metavar="FOCUS",
                    help="with --summarize: the question/focus for the synthesis "
                         "(strongly recommended — beats the generic summary)")
    _add_use_timeout(vp, convert_mode=True)
    vp.add_argument("--list-providers", action="store_true",
                    help="list providers available for the local parsing chain and "
                         "their credential status")
    vp.set_defaults(func=cmd_convert)

    # ── config ──
    cp = sub.add_parser("config", help="manage configuration (~/.config/eztool/config.json)")
    csub = cp.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("show", help="show all config values and the config file path"
                    ).set_defaults(func=cmd_config_show)
    cs = csub.add_parser("set", help="set a key, e.g. providers.doubao.api_key "
                                     "(prompts interactively if the value is omitted)")
    cs.add_argument("key")
    cs.add_argument("value", nargs="?")
    cs.set_defaults(func=cmd_config_set)
    cg = csub.add_parser("get", help="read a key (secrets are masked)")
    cg.add_argument("key")
    cg.set_defaults(func=cmd_config_get)
    cr = csub.add_parser("reset", help="reset a key to its default")
    cr.add_argument("key")
    cr.set_defaults(func=cmd_config_reset)
    ct = csub.add_parser("test", help="verify credentials of configured providers "
                                      "(--providers to select)")
    ct.add_argument("--providers", help="only test these providers (comma-separated)")
    ct.set_defaults(func=cmd_config_test)
    csub.add_parser("clear", help="delete the whole config file").set_defaults(func=cmd_config_clear)
    return p


# ── search / sources ──────────────────────────────────────────────────────────


def _search_category(args: argparse.Namespace) -> str:
    if args.image:
        return "image"
    if args.source:
        return "data"
    return "web"


def cmd_search(args: argparse.Namespace) -> None:
    category = _search_category(args)
    if args.list_providers:
        _print_category_providers(category)
        return
    if not args.query:
        raise UsageError("missing search query (or use --list-providers to see "
                         "available providers)")
    if args.summarize and category == "image":
        raise UsageError("--summarize does not apply to image search "
                         "(results have no text content)")
    cfg = cfgmod.load_config()
    if args.summarize:
        api.check_summarize(cfg)  # 缺配置 fail-fast（exit 2），不浪费搜索
    opts: dict = {"use": args.use, "all": args.all,
                  "count": args.count, "timeout": args.timeout,
                  "summarize": args.summarize}
    if args.source:
        opts["source"] = args.source
    if args.params:
        opts["params"] = args.params
    for pname in prov.category_params(category):
        v = getattr(args, pname, None)
        if v is not None:
            opts[pname] = v
    opts = {k: v for k, v in opts.items() if v is not None}
    resp = api.search(cfg, category, args.query, opts)
    if args.summarize and resp.answer:
        print(format_summary(resp.answer, resp.citations or [], resp.query))
    else:  # 未要求总结，或总结失败降级（stderr 已有 [summarize] failed 日志）
        print(_SEARCH_FORMATTERS[category](resp))


def cmd_sources(args: argparse.Namespace) -> None:
    print(format_sources(api.list_sources()))


def _print_category_providers(category: str) -> None:
    """列出该类别的 provider + 凭证状态（已配 / 匿名可用 / 未配将被跳过）。"""
    cfg = cfgmod.load_config()
    popts = api._provider_opts(cfg)
    chain = api._credentialed_chain(cfg, category, popts)
    print(f"{category} (default chain: {', '.join(chain) or '(empty)'})")
    for name in api.list_category_providers(category):
        cls = prov.SERVICES[name]
        status = []
        if cls.auth_required:
            status.append("credentials configured" if cls(popts).has_credentials()
                          else "no credentials (skipped by the default chain)")
        else:
            status.append("works anonymously")
        prio = (cls.priority or {}).get(category)
        if prio is not None:
            status.append(f"priority={prio}")
        print(f"  {name}: {'; '.join(status)}")


# ── fetch / convert ───────────────────────────────────────────────────────────


def _emit_content(content: str, out: str | None) -> None:
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"wrote {out} ({len(content)} chars)")
    else:
        print(content)


def _concat_pages(results) -> str:
    """多 URL 原始输出：单篇直接给正文；多篇用注释分隔符标注来源。"""
    if len(results) == 1:
        return results[0].content
    return "\n\n---\n\n".join(
        f"<!-- eztool: {r.url} [{r.provider}] -->\n\n{r.content}" for r in results
    )


def _pages_request(args, fallback: str) -> str:
    """fetch/convert --summarize 的 request：--query 优先，否则通用摘要。"""
    return args.query or fallback


def _emit_pages(args, cfg, results) -> None:
    """fetch/convert 输出收口：--summarize → AI 答案+引用（失败降级原文）。"""
    if not args.summarize:
        _emit_content(_concat_pages(results), args.out)
        return
    n = len(results)
    fallback = (f"Summarize the key points of this content."
                if n == 1 else
                f"Synthesize the key points across these {n} sources, noting "
                f"agreements and differences.")
    try:
        summary = api.summarize_pages(cfg, _pages_request(args, fallback), results)
    except ServiceError as e:  # LLM 失败降级：warning + 原始内容（exit 0）
        print(f"warning: summarize failed ({e.message}); returning raw content",
              file=sys.stderr)
        _emit_content(_concat_pages(results), args.out)
        return
    _emit_content(format_summary(summary.answer, summary.citations), args.out)


def cmd_fetch(args: argparse.Namespace) -> None:
    if args.list_providers:
        _print_category_providers("page")
        return
    for url in args.target:
        if urlparse(url).scheme not in ("http", "https"):
            raise UsageError(f"fetch only accepts http(s):// URLs; use eztool convert "
                             f"for local files: {url}")
    cfg = cfgmod.load_config()
    if args.summarize:
        api.check_summarize(cfg)  # 缺配置 fail-fast（exit 2），不浪费抓取
    results, errors = api.fetch_many(
        cfg, args.target, {"use": args.use, "timeout": args.timeout}
    )
    for url, e in errors:
        print(f"warning: {url} failed: {e}", file=sys.stderr)
    _emit_pages(args, cfg, results)


def cmd_convert(args: argparse.Namespace) -> None:
    if args.list_providers:
        _print_category_providers("file")
        return
    if not args.target:
        raise UsageError("missing file path argument (or use --list-providers)")
    if urlparse(args.target).scheme in ("http", "https"):
        raise UsageError(f"convert only accepts local files; use eztool fetch "
                         f"for URLs: {args.target}")
    cfg = cfgmod.load_config()
    if args.summarize:
        api.check_summarize(cfg)  # 缺配置 fail-fast（exit 2），不浪费解析
    result = api.convert(cfg, args.target,
                         {"use": args.use, "timeout": args.timeout})
    _emit_pages(args, cfg, [result])


# ── config ────────────────────────────────────────────────────────────────────


def _flat_keys() -> list[str]:
    """扁平化全部可设键；嵌套段（值为 dict）自动展开，无需硬编码名字。"""
    keys: list[str] = []

    def walk(prefix: str, node) -> None:
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(path, v)
            else:
                keys.append(path)

    walk("", cfgmod.DEFAULTS)
    return keys


def _check_key(key: str) -> None:
    if key not in cfgmod.KEY_HINTS and key not in _flat_keys():
        raise UsageError(f"unknown config key: {key} (see eztool config show for valid keys)")


def cmd_config_show(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    print(f"config file: {cfgmod.config_path()}")
    for key in _flat_keys():
        val = cfgmod.get_key(cfg, key)
        if val is None:
            shown = "(unset)"
        elif key in cfgmod.SECRET_KEYS:
            shown = cfgmod.mask_key(key, val)
        elif isinstance(val, list):
            shown = ",".join(str(v) for v in val)
        else:
            shown = str(val)
        print(f"{key} = {shown}")


def cmd_config_get(args: argparse.Namespace) -> None:
    _check_key(args.key)
    cfg = cfgmod.load_config()
    val = cfgmod.get_key(cfg, args.key)
    if val is None:
        print("(unset)")
    elif args.key in cfgmod.SECRET_KEYS:
        print(cfgmod.mask_key(args.key, val))
    elif isinstance(val, list):
        print(",".join(str(v) for v in val))
    else:
        print(val)


def cmd_config_set(args: argparse.Namespace) -> None:
    _check_key(args.key)
    cfg = cfgmod.load_config()
    if args.value is None:
        hint = cfgmod.KEY_HINTS.get(args.key, "")
        if args.key in cfgmod.SECRET_KEYS:
            raw = getpass.getpass(f"{args.key} ({hint}): ")
        else:
            raw = input(f"{args.key} ({hint}): ").strip()
    else:
        raw = args.value
    value = cfgmod.parse_value(args.key, raw)
    cfgmod.set_key(cfg, args.key, value)
    cfgmod.save_config(cfg)
    print(f"{args.key} = {cfgmod.mask_key(args.key, value)}")


def cmd_config_reset(args: argparse.Namespace) -> None:
    _check_key(args.key)
    cfg = cfgmod.load_config()
    default = cfgmod.DEFAULTS
    for part in args.key.split("."):
        default = default.get(part, None)
        if default is None:
            break
    cfgmod.set_key(cfg, args.key, default)
    cfgmod.save_config(cfg)
    print(f"reset {args.key} = {default}")


def cmd_config_clear(args: argparse.Namespace) -> None:
    path = cfgmod.config_path()
    if os.path.isfile(path):
        os.remove(path)
        print(f"deleted {path}")
    else:
        print(f"config file does not exist: {path}")


def cmd_config_test(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    popts = api._provider_opts(cfg)
    if args.providers:
        names = [n.strip() for n in args.providers.split(",") if n.strip()]
    else:
        names = sorted(prov.SERVICES)
    failed = False
    for name in names:
        svc = prov.SERVICES[name](popts)
        if not svc.has_credentials():
            print(f"{name}: no credentials configured")
            continue
        try:
            print(f"{name}: {svc.test_credentials()}")
        except EztoolError as e:
            print(f"{name}: failed — {e.message}", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except EztoolError as e:
        print(f"error: {e.message}", file=sys.stderr)
        if e.code:
            print(f"code: {e.code}", file=sys.stderr)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
