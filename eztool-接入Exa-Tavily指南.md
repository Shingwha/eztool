# eztool 接入 Exa + Tavily 完整指南（search.web + convert.page）

> 版本：2026-08-04 · 适用 eztool 0.2.x（config v3 结构）
> 目标：给 eztool 增加两个 AI 搜索后端——Tavily 与 Exa，分别接入 `search.web`（网页搜索）和 `convert.page`（URL → Markdown）两条能力链。
> 附带收益：两家都支持 `include_domains` 定向，**实测 Tavily 可直接搜到并抓取 mp.weixin.qq.com 公众号文章全文，无验证墙**（见 §3.5）。

---

## 0. 一分钟结论

| 事项 | Tavily | Exa |
|---|---|---|
| 接入类别 | search.web + convert.page | search.web + convert.page |
| 认证 | body 里 `api_key` 字段；**不配 key 自动走 keyless**（`X-Tavily-Access-Mode: keyless`，限速免费） | header `x-api-key`（必须配 key） |
| 免费额度 | 1000 credits/月，无需信用卡 | 注册送 $20，之后每月送 $10 |
| 主要成本 | basic 搜索 = 1 credit/次；advanced = 2 credits/次 | Search $7/1k 请求（≤10 结果，超出 $1/1k）；Contents $1/1k 页 |
| 亮点 | keyless 零成本接入、extract 连微信验证墙文章都能抓全文 | neural 语义搜索质量高、category 多样（news/company/publication/people） |

接入步骤概览（详见 §5–§7）：

```bash
# 1. 在 script/src/ezwork_tool/providers/ 下新建 tavily.py 与 exa.py（代码见 §5）
# 2. 在 providers/__init__.py 的 import 列表加 tavily, exa
# 3. 配置 config：providers.tavily.api_key / providers.exa.api_key + 回退链
eztool config set providers.tavily.api_key "tvly-xxxx"
eztool config set providers.exa.api_key "xxxx"
eztool config set search.web.providers '["exa","tavily","doubao","anysearch","deepseek"]'
eztool config set convert.page.providers '["markdown_new","tavily","exa","jina_reader","firecrawl"]'
# 4. 重装（见 §7.2）→ 测试
eztool config test --providers tavily,exa
eztool search "大模型 代码执行" --providers tavily
eztool convert "https://mp.weixin.qq.com/s/xxx" --providers tavily
```

---

## 1. eztool provider 机制速览（3 分钟看懂）

eztool 的扩展点只有一个：**Provider 子类 + `@register` 装饰器**。三个概念：

### 1.1 类别（category）
路由与参数归属的最小单元，命名 `<域>.<操作>`：

```
search.web / search.image / search.paper / search.data   ← 搜索域
convert.page / convert.file                              ← 转换域
```

每个类别有一条**回退链**（config 里 `search.web.providers` / `convert.page.providers` 列表）。请求按列表顺序依次尝试，前面失败自动换下一个。

### 1.2 Provider 基类（src/ezwork_tool/base.py）

```python
class Provider:
    name: str                    # provider 名（config 键、--providers 参数都用它）
    categories: frozenset        # 支持哪些类别，如 {"search.web", "convert.page"}
    category_params: dict        # {类别: {参数名: ParamSpec}}，自动生成 CLI 参数

    def search(self, cfg, query, opts) -> SearchResponse   # 搜索能力（默认不支持）
    def has_credentials(self, cfg) -> bool                  # config test 预检查
    def test_credentials(self, cfg) -> str                  # 发最小请求验证
    def fetch(self, url, timeout) -> FetchResult            # 转换能力（基类已实现）
    # fetch 内部调用这三个可覆写方法：
    #   build_headers() / _request(target, timeout) / parse_body(status, headers, body)
```

**两种实现模式**（本指南各用其一）：
- **搜索型**：参考 `providers/anysearch.py`——模块级 `_call_api()` 发 POST JSON（urllib 或 `http_post`），`_search()` 组装 `SearchResponse`，类里实现 `search()`。
- **转换型**：参考 `providers/firecrawl.py`——覆写 `build_headers()` + `_request()` + `parse_body()`，`fetch()` 复用基类。

### 1.3 数据结构

```python
@dataclass SearchResult: title, url, snippet, content(可选正文), extra, source
@dataclass SearchResponse: query, results, answer(可选), metadata
@dataclass FetchResult: provider, content(markdown), url, elapsed, tokens
```

### 1.4 关键约定（踩坑点）

- **全局参数不用声明**：`--count`（所有 search 类别）、`--timeout`、`--providers` 已由 CLI 提供，会自动出现在 opts 里。新 provider 的 `category_params` **不要**再声明 `count`/`timeout`，否则 argparse 参数名冲突。
- **category_params 同类别内不能重名**：registry 启动时校验，重名直接报错。
- `opts` 里拿参数：`opts.get("count")`（来自 `--count`）、`opts.get("include_domains")`（来自你声明的 `--include-domains`，argparse 自动把 `-` 转 `_`）。
- 新增 provider 文件后，必须手动加到 `providers/__init__.py` 的 import 列表（`from . import (...)` 是注册的副作用）。

---

## 2. 接入前准备（注册拿 key）

| 服务 | 注册地址 | 拿 key | 免费额度 |
|---|---|---|---|
| Tavily | https://app.tavily.com | Dashboard → API Keys | 1000 credits/月，无卡；**不注册也能用 keyless** |
| Exa | https://dashboard.exa.ai | API Keys 页 | 注册送 $20 + 每月 $10 |

Exa 必须注册。Tavily 可以先不注册，keyless 直接测通后再补 key 换正式额度。

---

## 3. Tavily API 详解（已实测）

### 3.1 端点

```
POST https://api.tavily.com/search    ← web 搜索
POST https://api.tavily.com/extract   ← URL 批量抓正文
```

### 3.2 认证（二选一，响应结构完全相同）

```bash
# 方式 A：keyless（无需注册，限速免费）
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" \
  -H "X-Tavily-Access-Mode: keyless" \
  -d '{"query":"最新AI新闻","max_results":3}'

# 方式 B：api_key（写在请求体里！不是 header）
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" \
  -d '{"query":"最新AI新闻","max_results":3,"api_key":"tvly-xxxx"}'
```

注意：Tavily 的 key 放在 **JSON body** 的 `api_key` 字段，不是 Authorization header。extract 同理。

### 3.3 POST /search 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索词 |
| `max_results` | int | 5 | 结果数，**0–20** |
| `search_depth` | string | `basic` | `basic`/`advanced`/`fast`/`ultra-fast`。advanced = 2 credits，其余 = 1 credit |
| `topic` | string | `general` | `general`/`news`/`finance`（news 适合实时新闻） |
| `time_range` | string | 无 | `day`/`week`/`month`/`year`（按发布时间过滤） |
| `start_date` / `end_date` | string | 无 | `YYYY-MM-DD` 精确日期范围 |
| `include_domains` | string[] | 无 | **只搜这些域名**，如 `["mp.weixin.qq.com"]`（替代 query 里的 site: 操作符） |
| `exclude_domains` | string[] | 无 | 排除域名 |
| `include_answer` | bool/string | false | 附带 LLM 生成回答：`true`/`basic` 快速版，`advanced` 详细版 |
| `include_raw_content` | bool/string | false | 附带每个结果的完整正文：`markdown`/`true` 返回 markdown，`text` 返回纯文本（更慢） |

### 3.4 POST /search 响应结构

```jsonc
{
  "query": "大模型 代码执行",
  "follow_up_questions": null,
  "answer": null,                      // include_answer=true 时有
  "images": [],
  "results": [
    {
      "url": "https://mp.weixin.qq.com/s/tNJARtn1jIayL5URg87Row",
      "title": "…",
      "content": "分块正文（≤500 字符/块，chunks 以 [...] 连接）",
      "score": 0.98,
      "published_date": "2026-07-30",
      "raw_content": "…"              // include_raw_content=true 时有
    }
  ]
}
```

### 3.5 实测记录（2026-08-04，keyless）

```
# 定向搜公众号 ✅ 返回真实微信文章，content 自带正文
curl -X POST https://api.tavily.com/search \
  -H "X-Tavily-Access-Mode: keyless" \
  -d '{"query":"大模型 代码执行","max_results":3,"include_domains":["mp.weixin.qq.com"]}'
# → results[0] = mp.weixin.qq.com/s/tNJARtn1jIayL5URg87Row（小红书 dots 开源 BigMac 一文），content 含正文开头

# extract 抓微信文章全文 ✅ 9231 字符 markdown，含图片链接，无验证墙
curl -X POST https://api.tavily.com/extract \
  -H "X-Tavily-Access-Mode: keyless" \
  -d '{"urls":["https://mp.weixin.qq.com/s/tNJARtn1jIayL5URg87Row"],"extract_depth":"basic"}'
```

**已知小坑**：部分 mp.weixin.qq.com 页面的 `title` 字段会原样返回 URL（页面本身缺 `<title>` 标签），但 `content` 正文不受影响。抓正文用 extract 更稳。

### 3.6 POST /extract 参数与响应

```jsonc
// 请求：urls 数组（单次最多 20 个 URL），extract_depth: basic|advanced
// 计费：basic = 5 URL/credit，advanced = 5 URL/2 credits（失败不扣费）
{ "urls": ["https://example.com"], "extract_depth": "basic", "api_key": "tvly-xxx" }

// 响应：
{
  "results": [
    { "url": "https://…", "title": "…", "raw_content": "# Markdown 正文…", "images": [] }
  ],
  "failed_results": [],   // 抓取失败的 URL 列表
  "response_time": 1.23,
  "request_id": "…"
}
```

### 3.7 计费汇总

- Search：`basic`/`fast`/`ultra-fast` = **1 credit/次**；`advanced` = **2 credits/次**
- Extract：basic = **5 URL/credit**；advanced = **5 URL/2 credits**；失败 URL 不扣费
- 免费 1000 credits/月；超了按 $0.008/credit 按量付费或升级套餐

---

## 4. Exa API 详解（OpenAPI v2，2026-08 抓取）

### 4.1 端点

```
POST https://api.exa.ai/search     ← neural 语义搜索
POST https://api.exa.ai/contents   ← 按 URL/ID 取全文
```

### 4.2 认证

```bash
curl -X POST https://api.exa.ai/search \
  -H "x-api-key: YOUR-EXA-API-KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"Latest research in LLMs","numResults":5}'
```

header 用 `x-api-key`（也支持 `Authorization: Bearer`）。

### 4.3 POST /search 参数（完整 schema）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索词（Exa 会做 prompt-engineered 语义理解） |
| `numResults` | int | 10 | **1–100** |
| `type` | string | `auto` | `instant`/`fast`/`auto`/`deep-lite`/`deep`/`deep-reasoning`（延迟与深度递增；auto 均衡推荐） |
| `category` | string | 无 | `company`/`publication`/`news`/`personal site`/`financial report`/`people`（其他字符串当 category hint） |
| `includeDomains` | string[] | 无 | 只搜这些域名（支持 `example.com/docs` 路径前缀、`*.example.com` 通配子域），**≤1200 条** |
| `excludeDomains` | string[] | 无 | 排除域名 |
| `startPublishedDate` / `endPublishedDate` | ISO8601 | 无 | 按发布时间过滤（格式 `YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM:SS.000Z`） |
| `contents` | object | 无 | 一次附带内容：`{"text": {"maxCharacters": ≤10000, "includeHtmlTags": false, "verbosity": "compact|standard|full"}, "highlights": true/false, "summary": true/false}`。**开 contents 会产生额外费用（见 §4.6）** |
| `excludeText` | string[] | 无 | 排除含这些词的页面 |

> 注：`startCrawlDate`/`endCrawlDate` 已废弃无效，别用。

### 4.4 POST /search 响应结构

```jsonc
{
  "requestId": "b5947044…",
  "results": [
    {
      "title": "…",
      "url": "https://mp.weixin.qq.com/s/…",
      "publishedDate": "2023-11-16T01:36:32.547Z",   // 估算的创建时间
      "author": "…",
      "id": "…",                    // 可用于 /contents 的 ids
      "image": "…", "favicon": "…",
      "text": "…",                  // 开 contents.text 时才有全文
      "highlights": ["…"],          // 开 contents.highlights 时才有
      "summary": "…"
    }
  ],
  "costDollars": { "total": 0.007, "search": { "neural": 0.007 } }
}
```

### 4.5 POST /contents 参数与响应

```jsonc
// 请求：urls（或 ids）数组 + 内容选项
{ "urls": ["https://arxiv.org/abs/2307.06435"], "text": {"maxCharacters": 10000} }
// text 可简写为 true（默认配置）；highlights: {"numSentences": 3} 也可叠加

// 响应：
{ "results": [ { "id": "…", "url": "…", "title": "…", "text": "全文…", "highlights": ["…"] } ] }
```

### 4.6 计费汇总

| 项 | 价格 |
|---|---|
| Search（≤10 结果） | **$7 / 1k 请求**（$0.007/次） |
| 超过 10 个结果的额外结果 | $1 / 1k 请求 |
| Deep Search / Deep-Reasoning Search | $12–15 / 1k 请求 |
| Contents | **$1 / 1k 页 / 每内容类型**（text、highlights、summary 各算一次） |
| 免费额度 | 注册送 **$20**，之后每月送 **$10** |

> 省钱提示：默认不开 `contents`（search 纯结果 $0.007/次）；需要正文时走 convert.page（contents 端点）按需取。

---

## 5. 代码实现

### 5.1 `script/src/ezwork_tool/providers/tavily.py`（新建，完整代码）

```python
"""Tavily 后端：search.web（POST /search）+ convert.page（POST /extract）。

Tavily 是面向 AI agent 的实时搜索 API：结果自带分块正文（content），
extract 端点抓任意 URL 正文（markdown，实测可过 mp.weixin.qq.com 验证墙）。

认证自动切换：
- 配置了 providers.tavily.api_key → 写入请求体 api_key 字段（正式额度）
- 未配置 → 自动加 X-Tavily-Access-Mode: keyless（限速免费，无需注册）

计费：basic/fast/ultra-fast 搜索 = 1 credit/次，advanced = 2 credits/次；
extract basic = 5 URL/credit。免费 1000 credits/月。
"""

from __future__ import annotations

import json
import time

from ..base import ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import CATEGORY_HTTP, NoResultsError, ServiceError
from ..http import http_post
from ..registry import register

API_BASE = "https://api.tavily.com"
SEARCH_URL = f"{API_BASE}/search"
EXTRACT_URL = f"{API_BASE}/extract"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RESULTS = 10
API_MAX_RESULTS = 20

_DEPTHS = ("basic", "advanced", "fast", "ultra-fast")
_TOPICS = ("general", "news", "finance")
_TIME_RANGES = ("day", "week", "month", "year")


def _api_key(cfg: dict) -> str | None:
    return cfg.get("providers", {}).get("tavily", {}).get("api_key")


def _post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    """POST JSON 到 Tavily，认证自动切换 keyless / api_key。"""
    if api_key:
        body["api_key"] = api_key
    headers = {"Content-Type": "application/json"}
    if not api_key:
        headers["X-Tavily-Access-Mode"] = "keyless"
    status, _hdrs, raw = http_post(
        url, headers, json.dumps(body).encode("utf-8"), int(timeout)
    )
    if status != 200:
        raise ServiceError(f"tavily returned HTTP {status}", CATEGORY_HTTP, http_code=status)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"tavily: invalid JSON response: {e}", CATEGORY_HTTP) from None


def _split_domains(raw) -> list[str] | None:
    """--include-domains 是逗号分隔字符串 → API 要数组。"""
    if not raw:
        return None
    if isinstance(raw, list):
        return [str(d).strip() for d in raw if str(d).strip()]
    return [d.strip() for d in str(raw).split(",") if d.strip()] or None


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    api_key = _api_key(cfg)

    try:
        count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
    except (TypeError, ValueError):
        count = DEFAULT_MAX_RESULTS
    count = max(1, min(count, API_MAX_RESULTS))

    try:
        timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    body: dict = {
        "query": query.strip(),
        "max_results": count,
        "search_depth": opts.get("search_depth") or "basic",
    }
    if opts.get("topic"):
        body["topic"] = opts["topic"]
    if opts.get("time_range"):
        body["time_range"] = opts["time_range"]
    inc = _split_domains(opts.get("include_domains"))
    exc = _split_domains(opts.get("exclude_domains"))
    if inc:
        body["include_domains"] = inc
    if exc:
        body["exclude_domains"] = exc

    data = _post_json(SEARCH_URL, body, api_key, timeout)

    results = [
        SearchResult(
            title=str(r.get("title") or r.get("url", "Untitled")),
            url=str(r.get("url", "")),
            snippet=str(r.get("content") or "")[:300],
            content=r.get("raw_content") or r.get("content"),
        )
        for r in (data.get("results") or [])
    ]
    if not results:
        raise NoResultsError("tavily: 未找到结果")

    return SearchResponse(
        query=query.strip(),
        results=results,
        answer=data.get("answer"),
        metadata={"total_results": len(results)},
    )


@register
class TavilyProvider(Provider):
    name = "tavily"
    categories = frozenset({"search.web", "convert.page"})
    category_params = {
        "search.web": {
            "search_depth": ParamSpec(
                choices=_DEPTHS,
                help="basic=1 credit / advanced=2 credits，fast/ultra-fast 低延迟",
            ),
            "topic": ParamSpec(choices=_TOPICS, help="general/news/finance"),
            "time_range": ParamSpec(
                choices=_TIME_RANGES, help="按发布时间过滤：day/week/month/year"
            ),
            "include_domains": ParamSpec(
                metavar="DOMAINS", help="只搜这些域名，逗号分隔，如 mp.weixin.qq.com"
            ),
            "exclude_domains": ParamSpec(metavar="DOMAINS", help="排除域名，逗号分隔"),
        },
    }

    def has_credentials(self, cfg: dict) -> bool:
        return True  # keyless 兜底，永远可用

    def test_credentials(self, cfg: dict) -> str:
        t0 = time.monotonic()
        data = _post_json(
            SEARCH_URL, {"query": "test", "max_results": 1}, _api_key(cfg), DEFAULT_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        mode = "keyless" if not _api_key(cfg) else "api_key"
        n = len(data.get("results") or [])
        return f"OK ({mode}, {n} results, {elapsed:.1f}s)"

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)

    # ── convert.page 能力（fetch 风格，参考 firecrawl.py）──

    def build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if not self.api_key:
            headers["X-Tavily-Access-Mode"] = "keyless"
        return headers

    def _request(self, target: str, timeout: int):
        body = {"urls": [target]}
        if self.api_key:
            body["api_key"] = self.api_key
        return http_post(
            EXTRACT_URL, self.build_headers(), json.dumps(body).encode("utf-8"), timeout
        )

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"tavily extract returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"tavily extract: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("raw_content"):
            raise ServiceError("tavily extract: 未取到正文", CATEGORY_HTTP)
        return results[0]["raw_content"]
```

### 5.2 `script/src/ezwork_tool/providers/exa.py`（新建，完整代码）

```python
"""Exa 后端：search.web（POST /search）+ convert.page（POST /contents）。

Exa 是 neural 语义搜索引擎（原 Metaphor）：搜索结果质量高，支持 category
（news/company/publication/people…）、includeDomains 定向、type 多档
（instant→deep-reasoning）、contents 一次附带全文。

认证：x-api-key header（config providers.exa.api_key，必须配置）。
免费：注册送 $20，之后每月送 $10；Search $7/1k 请求（≤10 结果），
Contents $1/1k 页/内容类型。默认不开 contents 省钱，需要正文走 convert.page。
"""

from __future__ import annotations

import json
import time

from ..base import ParamSpec, Provider, SearchResponse, SearchResult
from ..errors import CATEGORY_HTTP, NoResultsError, ServiceError
from ..http import http_post
from ..registry import register

API_BASE = "https://api.exa.ai"
SEARCH_URL = f"{API_BASE}/search"
CONTENTS_URL = f"{API_BASE}/contents"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RESULTS = 10
API_MAX_RESULTS = 100

_TYPES = ("instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning")
_CATEGORIES = ("company", "publication", "news", "personal site", "financial report", "people")


def _api_key(cfg: dict) -> str | None:
    return cfg.get("providers", {}).get("exa", {}).get("api_key")


def _post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    if not api_key:
        raise ServiceError("exa: 需要配置 providers.exa.api_key", CATEGORY_HTTP)
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    status, _hdrs, raw = http_post(
        url, headers, json.dumps(body).encode("utf-8"), int(timeout)
    )
    if status == 402:
        raise ServiceError("exa: 402 Payment Required（免费额度用尽）", CATEGORY_HTTP, http_code=402)
    if status != 200:
        raise ServiceError(f"exa returned HTTP {status}", CATEGORY_HTTP, http_code=status)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ServiceError(f"exa: invalid JSON response: {e}", CATEGORY_HTTP) from None


def _split_domains(raw) -> list[str] | None:
    if not raw:
        return None
    if isinstance(raw, list):
        return [str(d).strip() for d in raw if str(d).strip()]
    return [d.strip() for d in str(raw).split(",") if d.strip()] or None


def _iso_date(raw: str | None) -> str | None:
    """YYYY-MM-DD → ISO8601；已带时间直接透传。"""
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 10 and s[4] == "-":
        return s + "T00:00:00.000Z"
    return s


def _search(cfg: dict, query: str, opts: dict) -> SearchResponse:
    api_key = _api_key(cfg)

    try:
        count = int(opts.get("count") or DEFAULT_MAX_RESULTS)
    except (TypeError, ValueError):
        count = DEFAULT_MAX_RESULTS
    count = max(1, min(count, API_MAX_RESULTS))

    try:
        timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    body: dict = {
        "query": query.strip(),
        "numResults": count,
        "type": opts.get("type") or "auto",
    }
    if opts.get("category"):
        body["category"] = opts["category"]
    inc = _split_domains(opts.get("include_domains"))
    exc = _split_domains(opts.get("exclude_domains"))
    if inc:
        body["includeDomains"] = inc
    if exc:
        body["excludeDomains"] = exc
    ds = _iso_date(opts.get("date_start"))
    de = _iso_date(opts.get("date_end"))
    if ds:
        body["startPublishedDate"] = ds
    if de:
        body["endPublishedDate"] = de
    if opts.get("with_content"):  # 显式开全文（额外费用 $1/1k 页）
        body["contents"] = {"text": {"maxCharacters": 2000}, "highlights": True}

    data = _post_json(SEARCH_URL, body, api_key, timeout)

    results = []
    for r in (data.get("results") or []):
        text = r.get("text") or ""
        hl = r.get("highlights") or []
        snippet = "\n".join(hl) if hl else text[:300]
        results.append(
            SearchResult(
                title=str(r.get("title") or "Untitled"),
                url=str(r.get("url") or r.get("id") or ""),
                snippet=snippet,
                content=text or None,
            )
        )
    if not results:
        raise NoResultsError("exa: 未找到结果")

    return SearchResponse(
        query=query.strip(),
        results=results,
        metadata={
            "total_results": len(results),
            "cost_dollars": (data.get("costDollars") or {}).get("total"),
        },
    )


@register
class ExaProvider(Provider):
    name = "exa"
    categories = frozenset({"search.web", "convert.page"})
    category_params = {
        "search.web": {
            "type": ParamSpec(
                choices=_TYPES,
                help="延迟/深度档位：instant/fast/auto(默认)/deep-lite/deep/deep-reasoning",
            ),
            "category": ParamSpec(
                choices=_CATEGORIES, help="内容类别：company/publication/news/people 等"
            ),
            "include_domains": ParamSpec(
                metavar="DOMAINS", help="只搜这些域名，逗号分隔，如 mp.weixin.qq.com"
            ),
            "exclude_domains": ParamSpec(metavar="DOMAINS", help="排除域名，逗号分隔"),
            "date_start": ParamSpec(metavar="YYYY-MM-DD", help="只返回此日期后发布的"),
            "date_end": ParamSpec(metavar="YYYY-MM-DD", help="只返回此日期前发布的"),
            "with_content": ParamSpec(
                action="store_true", help="附带全文与 highlights（额外计费，$1/1k 页）"
            ),
        },
    }

    def has_credentials(self, cfg: dict) -> bool:
        return bool(_api_key(cfg))

    def test_credentials(self, cfg: dict) -> str:
        t0 = time.monotonic()
        data = _post_json(
            SEARCH_URL, {"query": "test", "numResults": 1}, _api_key(cfg), DEFAULT_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        n = len(data.get("results") or [])
        return f"OK ({n} results, {elapsed:.1f}s)"

    def search(self, cfg: dict, query: str, opts: dict) -> SearchResponse:
        return _search(cfg, query, opts)

    # ── convert.page 能力 ──

    def build_headers(self) -> dict:
        if not self.api_key:
            raise ServiceError("exa: 需要配置 providers.exa.api_key", CATEGORY_HTTP)
        return {"Content-Type": "application/json", "x-api-key": self.api_key}

    def _request(self, target: str, timeout: int):
        body = {"urls": [target], "text": {"maxCharacters": 10000}}
        return http_post(
            CONTENTS_URL, self.build_headers(), json.dumps(body).encode("utf-8"), timeout
        )

    def parse_body(self, status: int, headers, body: bytes) -> str:
        if status != 200:
            raise ServiceError(f"exa contents returned HTTP {status}", CATEGORY_HTTP)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise ServiceError(f"exa contents: invalid JSON: {e}", CATEGORY_HTTP) from None
        results = data.get("results") or []
        if not results or not results[0].get("text"):
            raise ServiceError("exa contents: 未取到正文", CATEGORY_HTTP)
        return results[0]["text"]
```

### 5.3 注册：修改 `script/src/ezwork_tool/providers/__init__.py`

在 import 列表加两个模块（顺序即注册顺序，会影响 `--list-providers` 与默认回退链候选的展示顺序）：

```python
from . import (  # noqa: F401  (side-effect: register)
    anysearch,
    arxiv,
    crossref,
    deepseek,
    doubao,
    exa,          # ← 新增
    firecrawl,
    jina_reader,
    markdown_new,
    mineru,
    openalex,
    pdfinspector,
    tavily,       # ← 新增
)

__all__ = [
    "anysearch", "arxiv", "crossref", "deepseek", "doubao",
    "exa", "firecrawl", "jina_reader", "markdown_new", "mineru",  # ← 加 exa
    "openalex", "pdfinspector", "tavily",                          # ← 加 tavily
]
```

---

## 6. 配置（config v3）

```bash
# Tavily key（可暂不配，先 keyless 白嫖）
eztool config set providers.tavily.api_key "tvly-xxxx"
# Exa key（必须）
eztool config set providers.exa.api_key "你的exa-key"

# 回退链：把新后端加进 web 与 page 链（顺序 = 优先级）
eztool config set search.web.providers '["exa","tavily","doubao","anysearch","deepseek"]'
eztool config set convert.page.providers '["markdown_new","tavily","exa","jina_reader","firecrawl"]'

# 验证
eztool config show | grep -E 'tavily|exa|search.web|convert.page'
```

可选的 per-provider 默认值（不设则用代码内默认）：

```bash
eztool config set providers.tavily.max_results 10
eztool config set providers.tavily.timeout 30
eztool config set providers.exa.timeout 30
```

---

## 7. 测试

### 7.1 源码直跑（开发期，最快）

```bash
cd /var/minis/skills/ezwork-tool/script
PYTHONPATH=src python3 -m ezwork_tool.cli config test --providers tavily,exa
PYTHONPATH=src python3 -m ezwork_tool.cli search "大模型 代码执行" --providers tavily --count 5
PYTHONPATH=src python3 -m ezwork_tool.cli search "大模型 代码执行" --providers exa --count 5
# 定向公众号（Tavily）
PYTHONPATH=src python3 -m ezwork_tool.cli search "AI 智能体" --providers tavily --include-domains mp.weixin.qq.com
# Exa 定向 + 全文
PYTHONPATH=src python3 -m ezwork_tool.cli search "AI 智能体" --providers exa --include-domains mp.weixin.qq.com --with-content
# 页面转换
PYTHONPATH=src python3 -m ezwork_tool.cli convert "https://mp.weixin.qq.com/s/xxx" --providers tavily
PYTHONPATH=src python3 -m ezwork_tool.cli convert "https://mp.weixin.qq.com/s/xxx" --providers exa
```

### 7.2 重装生效（skill 仓库即源码，改完重装给全局 eztool 用）

```bash
# 建议先跑一遍仓库测试（137 个用例，确认没破坏注册表）
PYTHONPATH=src python3 -m pytest tests/ -q
# 重装
uv tool install --force /var/minis/skills/ezwork-tool/script
# 确认新参数出现
eztool search web --help | grep -E 'include-domains|search-depth|--type|with-content'
eztool convert --list-providers
```

### 7.3 预期输出

- `eztool config test --providers tavily` → `tavily: OK (keyless, 1 results, 1.2s)`（未配 key 时）
- `eztool config test --providers exa` → `exa: OK (1 results, 0.9s)`
- 搜索输出与现有 provider 一致（统一 formatter 渲染）

---

## 8. 常见问题与排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `tavily: returned HTTP 429` | keyless 限速或额度用尽 | 稍等重试；注册拿 key 写入 config |
| `exa: 402 Payment Required` | Exa 免费额度用尽 | dashboard.exa.ai 看余额；充值或降级 `--type fast`（更便宜档） |
| `--include-domains` 报 unknown argument | 没重装或 provider 没注册成功 | 检查 `__init__.py` import；重装后 `eztool search web --help` 确认 |
| `category param 'x' already declared` | 与同类别其他 provider 参数重名 | 换参数名（本指南用的参数名已避让全局 `--count/--timeout`） |
| Tavily 搜索微信文章 title 是 URL | 微信页面缺 `<title>`，非 bug | 用 `--providers tavily` 的 content 字段或走 convert |
| 微信文章正文乱码/为空 | 个别页面需要 referer | 改用 Tavily extract（自带渲染与反爬处理） |
| Exa 搜索无正文 | 默认不开 contents（省钱设计） | 加 `--with-content`，或转 convert.page |

## 9. 排障辅助：直接看两家 API 行为

```bash
# Tavily keyless 搜索（不消耗任何 key）
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" -H "X-Tavily-Access-Mode: keyless" \
  -d '{"query":"test","max_results":1}'

# Exa（需要 key）
curl -X POST https://api.exa.ai/search \
  -H "x-api-key: $EXA_KEY" -H "Content-Type: application/json" \
  -d '{"query":"test","numResults":1}'
```

---

## 10. 参考链接

- Tavily 文档（llms.txt 友好）：https://docs.tavily.com/llms.txt · Search API：https://docs.tavily.com/documentation/api-reference/endpoint/search.md · Extract：https://docs.tavily.com/documentation/api-reference/endpoint/extract.md · 计费：https://docs.tavily.com/documentation/api-credits.md · keyless：https://docs.tavily.com/documentation/keyless.md
- Exa 文档：https://docs.exa.ai/llms.txt · Search 参考：https://docs.exa.ai/reference/search.md · OpenAPI：https://api.exa.ai/openapi.json · 定价：https://exa.ai/pricing
- eztool 仓库：`/var/minis/skills/ezwork-tool/`（源码在 `script/src/ezwork_tool/`，模板参考 `providers/anysearch.py` 与 `providers/firecrawl.py`）

---

*文档完 · 接入遇到问题随时把报错贴回来。*
