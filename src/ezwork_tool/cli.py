"""eztool 统一入口：search / fetch / convert / tags / config。

CLI 全部消费统一注册表（registry）：search 子命令的参数与 --backend 候选
由各服务商的 ``search_params`` / ``capabilities`` 声明自动生成——
新增服务商无需改这里。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import __version__
from . import api
from . import config as cfgmod
from .errors import EztoolError, UsageError
from .formatter import format_search, format_tags
from .registry import (
    all_search_params,
    create_service,
    search_services,
)
from .providers.anysearch import KNOWN_TAGS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eztool",
        description="统一搜索（doubao / deepseek / anysearch）+ URL 抓取转 Markdown。一个工具，一个 skill。",
    )
    p.add_argument("--version", action="version", version=f"eztool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # ── search ──────────────────────────────────────────────
    # 特有参数来自注册表：各搜索服务商声明的 search_params 自动展开
    sp = sub.add_parser("search", help="搜索（--backend 选后端，默认 auto 自动回退）")
    sp.add_argument("query", help="搜索词")
    backends = ("auto",) + tuple(search_services())
    sp.add_argument("--backend", choices=backends, default="auto",
                    help="后端；auto 按 search.providers 链回退（默认 anysearch→doubao→deepseek，免费优先）")
    sp.add_argument("--providers", help="覆盖搜索回退链，逗号分隔（仅 auto 模式生效）")
    sp.add_argument("--count", type=int, default=None,
                    help="结果条数（doubao≤50 / anysearch≤20 / deepseek 忽略）")
    sp.add_argument("--timeout", type=int, default=None, help="请求超时秒数（覆盖后端默认）")
    sp.add_argument("--full", action="store_true", help="显示完整正文而非 300 字预览")
    for pname, spec in all_search_params().items():
        kwargs = {"help": spec.help, "default": None}
        if spec.action == "store_true":
            kwargs["action"] = "store_true"
        else:
            kwargs["type"] = spec.type
            if spec.choices:
                kwargs["choices"] = spec.choices
            if spec.metavar:
                kwargs["metavar"] = spec.metavar
        sp.add_argument("--" + pname.replace("_", "-"), **kwargs)
    sp.set_defaults(func=cmd_search)

    # ── fetch ───────────────────────────────────────────────
    fp = sub.add_parser("fetch", help="抓取 URL 转 Markdown（markdown.new→jina→firecrawl 回退链，免费优先）")
    fp.add_argument("url", nargs="?", help="要抓取的 URL")
    fp.add_argument("--timeout", type=int, default=None, help="超时秒数（覆盖配置）")
    fp.add_argument("--providers", help="覆盖回退链，逗号分隔")
    fp.add_argument("--list-providers", action="store_true", help="列出可用 provider")
    fp.set_defaults(func=cmd_fetch)

    # ── convert ──────────────────────────────────────────────
    vp = sub.add_parser("convert", help="本地文件转 Markdown（markdown.new→MinerU 回退；MinerU 无 Token ≤10MB，配 Token ≤200MB/批量/HTML）")
    vp.add_argument("file", nargs="?", help="本地文件路径（PDF/DOCX/XLSX/图片/CSV/JSON 等）")
    vp.add_argument("--out", metavar="PATH", help="写入该文件而非输出到 stdout")
    vp.add_argument("--timeout", type=int, default=None, help="超时秒数（覆盖配置，默认 60）")
    vp.add_argument("--providers", help="覆盖回退链，逗号分隔")
    vp.add_argument("--list-providers", action="store_true", help="列出支持文件转换的 provider")
    vp.set_defaults(func=cmd_convert)

    # ── tags ────────────────────────────────────────────────
    tp = sub.add_parser("tags", help="列出 AnySearch 数据源标签")
    tp.set_defaults(func=cmd_tags)

    # ── config ──────────────────────────────────────────────
    cp = sub.add_parser("config", help="配置管理（~/.config/ezwork-tool/config.json）")
    csub = cp.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("path", help="显示配置文件路径").set_defaults(func=cmd_config_path)
    csub.add_parser("show", help="显示全部配置（secret 脱敏）").set_defaults(func=cmd_config_show)
    cs = csub.add_parser("set", help="设置键值，如 providers.doubao.api_key（省略值则交互输入）")
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
    ct.add_argument("--backend", choices=search_services())
    ct.set_defaults(func=cmd_config_test)
    return p


# ── search ─────────────────────────────────────────────────

def cmd_search(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    opts = {name: getattr(args, name, None) for name in all_search_params()}
    opts.update({
        "count": args.count, "timeout": args.timeout, "full": args.full,
        "providers": getattr(args, "providers", None),
    })
    resp = api.search(cfg, args.query, args.backend, opts)
    print(format_search(resp, full=args.full))


# ── fetch ──────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> None:
    if args.list_providers:
        print("providers: " + ", ".join(api.list_providers()))
        return
    if not args.url:
        raise UsageError("缺少 url 参数（或使用 --list-providers）")
    cfg = cfgmod.load_config()
    result = api.fetch(cfg, args.url, {"timeout": args.timeout, "providers": args.providers})
    print(result.content)


# ── convert ────────────────────────────────────────────────

def cmd_convert(args: argparse.Namespace) -> None:
    if args.list_providers:
        print("convert providers: " + ", ".join(api.list_convert_providers()))
        return
    if not args.file:
        raise UsageError("缺少 file 参数（或使用 --list-providers）")
    cfg = cfgmod.load_config()
    result = api.convert(cfg, args.file, {"timeout": args.timeout, "providers": args.providers})
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result.content)
        print(f"已写入 {args.out}（{len(result.content)} chars）")
    else:
        print(result.content)


# ── tags ───────────────────────────────────────────────────

def cmd_tags(args: argparse.Namespace) -> None:
    print(format_tags(KNOWN_TAGS))


# ── config ─────────────────────────────────────────────────

def _flat_keys() -> list[str]:
    """扁平化全部可设键；provider 子段（值为 dict）自动展开，无需硬编码名字。"""
    keys: list[str] = []
    for sec, sub in cfgmod.DEFAULTS.items():
        for k, v in sub.items():
            if isinstance(v, dict):
                for pk in v:
                    keys.append(f"{sec}.{k}.{pk}")
            else:
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
    names = [args.backend] if args.backend else search_services()
    failed = False
    for name in names:
        svc = create_service(name)
        if not svc.has_credentials(cfg):
            print(f"{name}: 未配置凭证")
            continue
        try:
            print(f"{name}: {svc.test_credentials(cfg)}")
        except EztoolError as e:
            print(f"{name}: 失败 — {e.message}", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


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
