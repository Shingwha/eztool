"""eztool 统一入口：search / fetch / tags / config。"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import __version__
from . import config as cfgmod
from .errors import EztoolError, UsageError
from .formatter import format_search, format_tags
from .search import run_search


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eztool",
        description="统一搜索（doubao / anysearch / deepseek）+ URL 抓取转 Markdown。一个工具，一个 skill。",
    )
    p.add_argument("--version", action="version", version=f"eztool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # ── search ──────────────────────────────────────────────
    sp = sub.add_parser("search", help="搜索（--backend 选后端，默认 auto 自动路由）")
    sp.add_argument("query", help="搜索词")
    sp.add_argument("--backend", choices=("auto", "doubao", "anysearch", "deepseek"),
                    default="auto", help="后端；auto 按已配置凭证路由（doubao→deepseek→anysearch 兜底）")
    sp.add_argument("--count", type=int, default=None,
                    help="结果条数（doubao≤50 / anysearch≤20 / deepseek 忽略）")
    sp.add_argument("--timeout", type=int, default=None, help="请求超时秒数（覆盖后端默认）")
    sp.add_argument("--full", action="store_true", help="显示完整正文而非 300 字预览")
    # [anysearch]
    sp.add_argument("--tag", metavar="TAG", help="[anysearch] 数据源标签（见 eztool tags）")
    sp.add_argument("--zone", choices=("cn", "intl"), help="[anysearch] 区域")
    sp.add_argument("--language", help="[anysearch] 语言，如 zh-CN")
    sp.add_argument("--params", help="[anysearch] 额外参数 JSON")
    sp.add_argument("--anonymous", action="store_true", help="[anysearch] 强制匿名模式")
    # [doubao]
    sp.add_argument("--image", action="store_true", help="[doubao] 图片搜索")
    sp.add_argument("--sites", help="[doubao] 限定域名，| 分隔")
    sp.add_argument("--block-hosts", help="[doubao] 排除域名，| 分隔")
    sp.add_argument("--time-range",
                    help="[doubao] OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD")
    sp.add_argument("--need-content", action="store_true", help="[doubao] 只返回带正文的结果")
    sp.add_argument("--need-url", action="store_true", help="[doubao] 只返回带落地链接的结果")
    sp.add_argument("--content-formats", choices=("text", "markdown"), help="[doubao] 正文格式")
    sp.add_argument("--industry", choices=("finance", "game", "gov"), help="[doubao] 行业搜索")
    sp.add_argument("--query-rewrite", action="store_true", help="[doubao] 查询改写（更慢）")
    sp.add_argument("--auth-info-level", type=int, choices=(0, 1),
                    help="[doubao] 1=仅高权威来源")
    sp.add_argument("--width-min", type=int, help="[doubao image] 最小宽度")
    sp.add_argument("--width-max", type=int, help="[doubao image] 最大宽度")
    sp.add_argument("--height-min", type=int, help="[doubao image] 最小高度")
    sp.add_argument("--height-max", type=int, help="[doubao image] 最大高度")
    sp.add_argument("--shapes", choices=("横长方形", "竖长方形", "方形"), help="[doubao image] 图片形状")
    sp.set_defaults(func=cmd_search)

    # ── fetch ───────────────────────────────────────────────
    fp = sub.add_parser("fetch", help="抓取 URL 转 Markdown（firecrawl→markdown.new→jina 回退链）")
    fp.add_argument("url", nargs="?", help="要抓取的 URL")
    fp.add_argument("--timeout", type=int, default=None, help="超时秒数（覆盖配置）")
    fp.add_argument("--providers", help="覆盖回退链，逗号分隔")
    fp.add_argument("--list-providers", action="store_true", help="列出可用 provider")
    fp.set_defaults(func=cmd_fetch)

    # ── tags ────────────────────────────────────────────────
    tp = sub.add_parser("tags", help="列出 AnySearch 数据源标签")
    tp.set_defaults(func=cmd_tags)

    # ── config ──────────────────────────────────────────────
    cp = sub.add_parser("config", help="配置管理（~/.config/ezwork-tool/config.json）")
    csub = cp.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("path", help="显示配置文件路径").set_defaults(func=cmd_config_path)
    csub.add_parser("show", help="显示全部配置（secret 脱敏）").set_defaults(func=cmd_config_show)
    cs = csub.add_parser("set", help="设置键值，如 doubao.api_key（省略值则交互输入）")
    cs.add_argument("key")
    cs.add_argument("value", nargs="?")
    cs.set_defaults(func=cmd_config_set)
    cg = csub.add_parser("get", help="读取键值（secret 脱敏）")
    cg.add_argument("key")
    cg.set_defaults(func=cmd_config_get)
    cr = csub.add_parser("reset", help="重置键为默认值")
    cr.add_argument("key")
    cr.set_defaults(func=cmd_config_reset)
    csub.add_parser("clear", help="删除整个配置文件").set_defaults(func=cmd_config_clear)
    ct = csub.add_parser("test", help="验证已配置后端的凭证（--backend 只测一个）")
    ct.add_argument("--backend", choices=("doubao", "anysearch", "deepseek"))
    ct.set_defaults(func=cmd_config_test)
    csub.add_parser("import-legacy", help="从旧工具（doubao-websearch 等）导入已有配置") \
        .set_defaults(func=cmd_config_import)
    return p


# ── search ─────────────────────────────────────────────────

def cmd_search(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    opts = {
        "count": args.count, "timeout": args.timeout, "full": args.full,
        "tag": args.tag, "zone": args.zone, "language": args.language,
        "params": args.params, "anonymous": args.anonymous,
        "image": args.image, "sites": args.sites, "block_hosts": args.block_hosts,
        "time_range": args.time_range, "need_content": args.need_content,
        "need_url": args.need_url, "content_formats": args.content_formats,
        "industry": args.industry, "query_rewrite": args.query_rewrite,
        "auth_info_level": args.auth_info_level,
        "width_min": args.width_min, "width_max": args.width_max,
        "height_min": args.height_min, "height_max": args.height_max,
        "shapes": args.shapes,
    }
    resp = run_search(cfg, args.query, args.backend, opts)
    print(format_search(resp, full=args.full))


# ── fetch ──────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> None:
    from . import fetch as fmod

    if args.list_providers:
        print("providers: " + ", ".join(fmod.list_providers()))
        return
    if not args.url:
        raise UsageError("缺少 url 参数（或使用 --list-providers）")
    cfg = cfgmod.load_config()
    result = fmod.fetch(cfg, args.url, {"timeout": args.timeout, "providers": args.providers})
    print(result.content)


# ── tags ───────────────────────────────────────────────────

def cmd_tags(args: argparse.Namespace) -> None:
    from .search.anysearch import KNOWN_TAGS

    print(format_tags(KNOWN_TAGS))


# ── config ─────────────────────────────────────────────────

def _flat_keys() -> list[str]:
    keys: list[str] = []
    for sec, sub in cfgmod.DEFAULTS.items():
        if sec == "fetch":
            keys.append("fetch.providers")
            keys.append("fetch.timeout")
            for p in ("firecrawl", "markdown", "jina"):
                for k in cfgmod.DEFAULTS["fetch"][p]:
                    keys.append(f"fetch.{p}.{k}")
        else:
            for k in sub:
                keys.append(f"{sec}.{k}")
    return keys


def _check_key(key: str) -> None:
    if key not in cfgmod.KEY_HINTS:
        raise UsageError(f"未知配置键: {key}（可用键见 eztool config show）")


def cmd_config_path(args: argparse.Namespace) -> None:
    print(cfgmod.config_path())


def cmd_config_show(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    for key in _flat_keys():
        val = cfgmod.get_key(cfg, key)
        if val is None:
            shown = "(unset)"
        elif key in cfgmod.SECRET_KEYS:
            shown = cfgmod.mask_key(key, val)
        else:
            shown = str(val)
        print(f"{key} = {shown}")


def cmd_config_get(args: argparse.Namespace) -> None:
    _check_key(args.key)
    cfg = cfgmod.load_config()
    val = cfgmod.get_key(cfg, args.key)
    if args.key in cfgmod.SECRET_KEYS:
        print(cfgmod.mask_key(args.key, val) if val is not None else "(unset)")
    else:
        print(val if val is not None else "(unset)")


def cmd_config_set(args: argparse.Namespace) -> None:
    _check_key(args.key)
    cfg = cfgmod.load_config()
    if args.value is None:
        hint = cfgmod.KEY_HINTS[args.key]
        if args.key in cfgmod.SECRET_KEYS:
            raw = getpass.getpass(f"{args.key}（{hint}）: ")
        else:
            raw = input(f"{args.key}（{hint}）: ").strip()
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
    print(f"已重置 {args.key} = {default}")


def cmd_config_clear(args: argparse.Namespace) -> None:
    path = cfgmod.config_path()
    if os.path.isfile(path):
        os.remove(path)
        print(f"已删除 {path}")
    else:
        print(f"配置文件不存在: {path}")


def cmd_config_test(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    backends = [args.backend] if args.backend else ["doubao", "anysearch", "deepseek"]
    failed = False
    for name in backends:
        mod = _backend_module(name)
        if not mod.has_credentials(cfg):
            print(f"{name}: 未配置凭证（anysearch 可匿名使用）")
            continue
        try:
            print(f"{name}: {mod.test_credentials(cfg)}")
        except EztoolError as e:
            print(f"{name}: 失败 — {e.message}", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


def cmd_config_import(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    imported = cfgmod.import_legacy(cfg)
    if not imported:
        print("未找到旧工具配置文件")
        return
    cfgmod.save_config(cfg)
    for line in imported:
        print(line)


def _backend_module(name: str):
    if name == "doubao":
        from .search import doubao as mod
    elif name == "anysearch":
        from .search import anysearch as mod
    else:
        from .search import deepseek as mod
    return mod


# ── main ───────────────────────────────────────────────────

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
