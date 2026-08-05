# 参考指南（guide.md）——配置 / provider / 架构

> 需要精确配置项、provider 能力表或改 eztool 本身时读本文件。日常使用看 SKILL.md。

## 配置

所有配置集中在 `~/.config/ezwork-tool/config.json`（`eztool config show` 首行显示实际路径）。

| 命令 | 作用 |
|---|---|
| `eztool config set <key> [值]` | 设置配置项（省略值则交互输入，密钥隐藏显示） |
| `eztool config get <key>` | 读取单个配置（密钥脱敏） |
| `eztool config show` | 查看全部配置 + 配置文件路径 |
| `eztool config test [--providers <名>]` | 用真实请求验证凭证（默认遍历全部 provider） |
| `eztool config reset <key>` | 恢复默认值 |
| `eztool config clear` | 删除整个配置文件 |

结构：`providers.<name>.*`（凭证/超时，**键由 provider 的 `config` 声明自动生成**——`config show` 列出的就是全部可设键）+ `search.<类别>` / `convert.<类别>` 段（回退链 + 缺省超时）。

### 各 provider 凭证要求

| provider | 必须配置 | 不配会怎样 |
|---|---|---|
| `doubao` | `providers.doubao.api_key`，或 `ak` + `sk` | 默认链**自动跳过** doubao；`--providers doubao` 点名则报错；`search image` 不可用 |
| `deepseek` | `providers.deepseek.api_key` | 默认链自动跳过 |
| `exa` | `providers.exa.api_key` | 不在默认链（`--providers exa` 或配置链使用）；点名未配报错 |
| `anysearch` / `tavily` / `firecrawl` / `jina_reader` / `markdown_new` / `mineru` / `anydoc` | 无 | 匿名可用（限流）；配 key 提额度；mineru 配 Token 自动升级 v4 Precision API |

### 类别段（回退链配置）

- `search.web.providers` / `search.image.providers` / `search.data.providers`
- `convert.page.providers` / `convert.file.providers`
- 值：逗号分隔字符串（`eztool config set search.web.providers "doubao,anysearch"`）
- **未显式配置时**，默认链 = 类别内 provider 按声明的 `priority` 排序自动派生（新 provider 声明 priority 即自动进链），无需维护

## provider 能力总表

类别是路由单元（`<域>.<操作>`）：回退链按类别过滤、CLI 子命令参数面按类别生成，全部由 provider 注册表驱动。

| provider | search.web | search.image | search.data | convert.page | convert.file | 默认链 priority | 特有 CLI 参数 |
|---|---|---|---|---|---|---|---|
| `doubao` | ✅ | ✅ | | | | web:10 image:10 | image: width_min/max height_min/max shapes |
| `anysearch` | ✅ | | ✅ | ✅ | | web:20 data:10 page:30 | data: tag / params |
| `deepseek` | ✅ | | | | | web:30 | — |
| `markdown_new` | | | | ✅ | ✅ | page:10 file:20 | — |
| `jina_reader` | | | | ✅ | | page:20 | — |
| `tavily` | ✅ | | | ✅ | | page:40（web 不进链） | — |
| `firecrawl` | | | | ✅ | | page:50 | — |
| `mineru` | | | | ✅ | ✅ | file:30（page 不进链） | — |
| `anydoc` | | | | | ✅ | file:10 | — |
| `exa` | ✅ | | | ✅ | | 不声明（不进链） | — |

> 通用参数（所有搜索类别）：`--count`（每个 provider 的结果数）、`--timeout`、`--providers`（并行名单）。provider 特有参数（`params` 声明）自动并入对应子命令。

## 执行语义

- **默认（不带 `--providers`）**：config 回退链——按序尝试，失败自动换下一个；**auth_required 且未配凭证的 provider 自动跳过**（匿名可用的一律进链）。
- **`--providers a,b`**：并行跑指定 provider。search 合并去重（按 URL）+ 来源标注（`[doubao]`），单个失败不影响其他，全失败报错；convert 并行取先成功者；指定 1 个 = 单跑（显式点名不做凭证跳过，未配报错）。
- **质量门**（URL 抓取特有）：内容开头 200 字符命中拦截话术（环境异常/captcha 等）且 <800 字符 → 拦截页，链继续回退；800–1500 → 可疑后备 + 警告；≥1500 或未命中 → 正常。微信验证页即典型"假成功"，自动回退 tavily。
- 未知 provider 名 → 用法错误（exit 2）；全部失败 → `all providers failed`（exit 1）。

## 输出与退出码

统一 Markdown（`### Answer` 可选 + `### Results (N)` + `---` 元数据尾行）；结果**完整输出不截断**，无 `--json`。退出码：0 成功 / 1 业务失败 / 2 用法或凭证缺失。stderr 的 `[provider] OK (耗时, 结果数)` 是执行日志。

## 架构（开发者）

```
script/src/ezwork_tool/
├── cli.py        # 命令面（search web/image/data/tags + convert + config）+ 输出格式
├── api.py        # search_category / convert：回退链 + 并行 fan-out 路由
├── provider.py   # Provider 基类 + 元数据（config/params/priority/auth_required）+ 注册表
├── config.py     # 配置读写工具；DEFAULTS/SECRET_KEYS/KEY_HINTS 由元数据自动生成
├── util.py       # 异常体系 + HTTP 工具 + 内容质量门
└── providers/    # 10 个 provider；__init__.py 是唯一注册点（import 即注册）
```

**新增 provider 两步**：① 写 `providers/foo.py`（类声明 `name`/`categories`/`config`/`params`/`priority`/`auth_required` + 实现能力方法）；② `providers/__init__.py` import 加一行。config 键、CLI 参数、默认链、`config show`、`--list-providers` 全部自动出现。**加配置键/参数只改 provider 文件一处**。

**新 provider 的元数据语义**：
- `config`：`{键: {default, secret, hint}}`（相对 `providers.<name>` 段）→ 生成 DEFAULTS/SECRET_KEYS/KEY_HINTS
- `params`：`{类别: {参数名: ParamSpec}}` → 生成子命令 CLI 参数面（同名跨 provider 冲突注册期报错）
- `priority`：`{类别: 排序值}` → 自动派生默认回退链（不声明 = 不进链，如 exa）
- `auth_required`：True = 必须配凭证（默认链跳过未配的）；False = 匿名可用

## 开发 / 测试 / 更新

```bash
cd script
PYTHONPATH=src python -m ezwork_tool.cli --help        # 免安装运行
PYTHONPATH=src python -m unittest discover tests -q    # 全部测试（116 个）
uv tool install ".[local]" --force --reinstall          # 安装（[local] 带 firecrawl-anydoc；务必先 uv cache clean）
```

更新流程：`git pull` → `cd script` → `uv cache clean ezwork-tool && uv tool install ".[local]" --force --reinstall`。

测试策略：`test_search/test_convert`（回退链 + 并行语义）、`test_cli`（命令面）、`test_config`（元数据生成）、`test_provider`（注册/冲突/默认链）、`test_quality`（质量门）、`test_mineru/test_anydoc`（provider 协议细节）。
