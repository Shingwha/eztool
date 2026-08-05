"""eztool 统一入口：search（web/image/data/tags）+ convert + config。

命令面 = 域（顶层 3 个）+ 子命令（搜索类别）。参数面：
- 通用参数（所有搜索 provider 都认）：--count / --timeout / --providers
- 类别特殊参数：由各 provider 的 ``params`` 声明自动并入（同名冲突注册期报错）

``--providers a,b`` = **并行**名单（1 个 = 单跑）；不指定 = 走 config 回退链。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import __version__
from . import api
from . import config as cfgmod
from . import provider as prov
from .provider import SearchResponse
from .providers.anysearch import KNOWN_TAGS
from .util import EztoolError, ServiceError, UsageError

# 类别 → 子命令说明（search 子命令自动生成时的 help）
_CATEGORY_HELP = {
    "search.web": "通用网页搜索（默认链 doubao→anysearch→deepseek，失败自动换）",
    "search.image": "图片搜索（直链 + 尺寸/形状元数据）",
    "search.data": "专业数据源搜索（--tag 定向数据源）",
}

_COUNT_CATEGORIES = {"search.web", "search.image", "search.data"}


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eztool",
        description="搜索（web/image/data/tags）+ 转换（URL 或本地文件 → Markdown）+ 配置。一个命令完成搜索、读取与转格式。",
    )
    p.add_argument("--version", action="version", version=f"eztool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # ── search 域：子命令由 provider 注册表生成 ──
    sp = sub.add_parser("search", help="搜索域：web / image / data / tags")
    ssub = sp.add_subparsers(dest="search_cmd", required=True)
    search_categories = sorted({c for cls in prov.SERVICES.values()
                                for c in cls.categories if c.startswith("search.")})
    for category in search_categories:
        name = category.split(".")[1]
        csp = ssub.add_parser(name, help=_CATEGORY_HELP.get(category, category))
        csp.add_argument("query", help="搜索词")
        csp.add_argument("--providers", metavar="A,B",
                         help="并行跑指定 provider（逗号分隔；1 个 = 单跑；缺省走 config 回退链）")
        if category in _COUNT_CATEGORIES:
            csp.add_argument("--count", type=int, default=None,
                             help="每个 provider 的结果条数（覆盖各 provider 默认值）")
        csp.add_argument("--timeout", type=int, default=None,
                         help="请求超时秒数（覆盖配置）")
        csp.add_argument("--list-providers", action="store_true",
                         help=f"列出 {name} 类别可用 provider 及凭证状态")
        for pname, spec in prov.category_params(category).items():
            _add_param(csp, pname, spec)
        csp.set_defaults(func=cmd_search, category=category)

    # tags：列出全部数据源标签（无参数）
    tp = ssub.add_parser("tags", help="列出全部数据源标签")
    tp.set_defaults(func=cmd_tags)

    # ── convert 域：URL 或本地文件 → Markdown（运行时自动识别）──
    vp = sub.add_parser("convert", help="URL 或本地文件 → Markdown（http(s):// 走在线抓取链，本地路径走本地解析链）")
    vp.add_argument("target", nargs="?", help="URL（http/https）或本地文件路径")
    vp.add_argument("--out", metavar="PATH", help="写入该文件而非输出到 stdout")
    vp.add_argument("--timeout", type=int, default=None, help="超时秒数（覆盖配置）")
    vp.add_argument("--providers", metavar="A,B",
                    help="并行跑指定 provider（取先成功者；1 个 = 单跑；缺省走 config 回退链）")
    vp.add_argument("--list-providers", action="store_true",
                    help="列出两类转换链的可用 provider 及凭证状态")
    vp.set_defaults(func=cmd_convert)

    # ── config 域 ──
    cp = sub.add_parser("config", help="配置管理（~/.config/ezwork-tool/config.json）")
    csub = cp.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("show", help="显示全部配置 + 配置文件路径").set_defaults(func=cmd_config_show)
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
    ct = csub.add_parser("test", help="验证已配置 provider 的凭证（--providers 可指定单个）")
    ct.add_argument("--providers", help="只测指定 provider（逗号分隔）")
    ct.set_defaults(func=cmd_config_test)
    csub.add_parser("clear", help="删除整个配置文件").set_defaults(func=cmd_config_clear)
    return p


# ── search ─────────────────────────────────────────────────────────────────

def cmd_search(args: argparse.Namespace) -> None:
    if args.list_providers:
        _print_category_providers(args.category)
        return
    cfg = cfgmod.load_config()
    opts = {pname: getattr(args, pname, None) for pname in prov.category_params(args.category)}
    for k in ("count", "timeout", "providers"):
        v = getattr(args, k, None)
        if v is not None:
            opts[k] = v
    resp = api.search_category(cfg, args.category, args.query, opts)
    if args.category == "search.image":
        print(format_image(resp))
    elif args.category == "search.data":
        print(format_data(resp))
    else:
        print(format_search(resp))


def _print_category_providers(category: str) -> None:
    """列出该类别的 provider + 凭证状态（已配 / 匿名可用 / 未配将被跳过）。"""
    cfg = cfgmod.load_config()
    section = api._section(cfg, category)
    chain = api._chain_providers(section, category)
    print(f"{category}（默认链: {', '.join(chain) or '(空)'}）")
    for name in api.list_category_providers(category):
        cls = prov.SERVICES[name]
        status = []
        if cls.auth_required:
            has_key = cfgmod.get_key(cfg, f"providers.{name}.api_key")
            status.append("已配凭证" if has_key else "未配凭证（默认链会跳过）")
        else:
            status.append("匿名可用")
        prio = (cls.priority or {}).get(category)
        if prio is not None:
            status.append(f"priority={prio}")
        print(f"  {name}: {'; '.join(status)}")


# ── tags ───────────────────────────────────────────────────────────────────

def cmd_tags(args: argparse.Namespace) -> None:
    print(format_tags(KNOWN_TAGS))


# ── convert ────────────────────────────────────────────────────────────────

def cmd_convert(args: argparse.Namespace) -> None:
    if args.list_providers:
        _print_category_providers("convert.page")
        _print_category_providers("convert.file")
        return
    if not args.target:
        raise UsageError("缺少 target 参数（URL 或本地文件路径；或使用 --list-providers）")
    cfg = cfgmod.load_config()
    result = api.convert(cfg, args.target,
                         {"timeout": args.timeout, "providers": args.providers})
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result.content)
        print(f"已写入 {args.out}（{len(result.content)} chars）")
    else:
        print(result.content)


# ── config ─────────────────────────────────────────────────────────────────

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
    if key not in cfgmod.KEY_HINTS:
        raise UsageError(f"未知配置键: {key}（可用键见 eztool config show）")


def cmd_config_show(args: argparse.Namespace) -> None:
    cfg = cfgmod.load_config()
    print(f"config file: {cfgmod.config_path()}")
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
    if args.providers:
        names = [n.strip() for n in args.providers.split(",") if n.strip()]
    else:
        names = sorted(prov.SERVICES)
    failed = False
    for name in names:
        svc = prov.SERVICES[name]()
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


# ── 输出格式（唯一格式：Markdown，结果默认完整输出不截断）────────────────────

def _one_line(text: str) -> str:
    """把多行摘要压缩成单行。"""
    return " ".join(text.split())


def _meta_footer(meta: dict | None) -> str:
    meta = meta or {}
    meta_parts = [f"backend: {meta.get('backend', '?')}"]
    if meta.get("total_results") is not None:
        meta_parts.append(f"total: {meta['total_results']}")
    if meta.get("search_time_ms") is not None:
        meta_parts.append(f"{meta['search_time_ms'] / 1000:.2f}s")
    if meta.get("request_id"):
        meta_parts.append(f"request_id: {meta['request_id']}")
    return "---\n" + " · ".join(meta_parts)


def format_search(resp: SearchResponse) -> str:
    merged = "," in str((resp.metadata or {}).get("backend", ""))
    lines: list[str] = [f"## Search Results: {resp.query}", ""]
    if resp.answer:
        lines += ["### Answer", "", resp.answer.strip(), ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            title = r.title or r.url or f"(no title {i})"
            line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
            if merged and r.source:
                line += f" **[{r.source}]**"
            if r.snippet:
                line += f" — {_one_line(r.snippet)}"
            lines.append(line)
            if r.content:
                lines.append(f"   {r.content}")
            if r.extra:
                extra = " · ".join(f"{k}={v}" for k, v in r.extra.items() if v)
                if extra:
                    lines.append(f"   _{extra}_")
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_image(resp: SearchResponse) -> str:
    """图片结果：直链（可渲染）+ 尺寸/形状元数据。"""
    lines: list[str] = [f"## Images: {resp.query}", ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            line = f"{i}. ![img]({r.url})"
            extra = r.extra or {}
            dims: list[str] = []
            if extra.get("width") or extra.get("height"):
                dims.append(f"{extra.get('width', '?')}×{extra.get('height', '?')}")
            if extra.get("shape"):
                dims.append(str(extra["shape"]))
            if extra.get("score") is not None:
                dims.append(f"score={extra['score']}")
            if dims:
                line += f" — {' · '.join(dims)}"
            if r.title:
                line += f" — {_one_line(r.title)}"
            lines.append(line)
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_data(resp: SearchResponse) -> str:
    """专业数据源结果：带来源标注（provider 名）。"""
    lines: list[str] = [f"## Data Results: {resp.query}", ""]
    if resp.results:
        lines += [f"### Results ({len(resp.results)})", ""]
        for i, r in enumerate(resp.results, 1):
            title = r.title or r.url or f"(no title {i})"
            line = f"{i}. [{title}]({r.url})" if r.url else f"{i}. {title}"
            if r.source:
                line += f" **[{r.source}]**"
            if r.snippet:
                line += f" — {_one_line(r.snippet)}"
            lines.append(line)
        lines.append("")
    lines.append(_meta_footer(resp.metadata))
    return "\n".join(lines)


def format_tags(tags: list[tuple[str, str]]) -> str:
    lines = ["## Available data source tags (AnySearch)", ""]
    for name, desc in tags:
        lines.append(f"- `{name}` — {desc}")
    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────────

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
