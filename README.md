<!-- README.md -->
# Clash 多端口订阅分流

> 把多个订阅、自建节点和分享链接合成一份 mihomo 配置，每个端口走各自的订阅和规则。支持 Clash Verge Rev。

一个 mihomo 实例同时开多个本地端口，每个端口绑定自己的订阅、分组和规则。
支持订阅链接、自己写的节点 YAML，以及直接粘贴 `vless://`、`vmess://` 等分享链接。

## 快速开始

先装一次 [uv](https://docs.astral.sh/uv/)（Windows：`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`，macOS/Linux：`curl -LsSf https://astral.sh/uv/install.sh | sh`，或 `pip install uv`），然后在**任意空目录**运行：

```bash
uvx --from git+https://github.com/geezhu/clash-subscription-merge clash-merge
```

按提示回答即可，最后得到 `merged.yaml`，导入 Clash Verge Rev 使用。

<details>
<summary>其他运行方式</summary>

```bash
uv run merge.py                                       # 已克隆仓库：uv 自动装依赖
pip install ruamel.yaml requests && python merge.py   # 不用 uv
```

更新到最新版：`uvx --refresh --from git+... clash-merge`。
</details>

## 使用流程

1. **第一次运行**会询问：每个来源的名字、订阅链接（或 `local`）、端口，并保存为 `sources.yaml`。
2. **之后再运行**会显示摘要并询问「是否复用」，回答 `n` 可重新输入（旧文件备份为 `sources.yaml.bak`）。
3. 输出 `merged.yaml`，所有文件都生成在**当前目录**。

### 添加节点的三种方式

| 方式 | 怎么写 | 适合 |
|---|---|---|
| 订阅链接 | `url: https://...` | 机场订阅 |
| 分享链接 | `url: local` + `links: nodes.txt`（一行一个 `vless://` 等链接） | 自己搭的节点，不想手写 YAML |
| YAML 片段 | `url: local` + `snippet: xxx.yaml` | 已经有写好的节点 YAML |

分享链接支持 vless、vmess、trojan、ss、hysteria2、tuic、anytls，详见 [sources.yaml 与订阅片段](docs/04-snippets-and-import.md)。
交互创建时选「分享链接文件」，不存在 `nodes.txt` 的话可以直接粘贴链接，脚本替你生成。

### 导入使用

- **Clash Verge Rev**：新建本地配置，导入 `merged.yaml`，然后切换到它。
- **直接用 mihomo**：`mihomo -f merged.yaml -d <工作目录>`。

规则集和订阅会在 mihomo **首次启动时自动下载**到工作目录（`./rules/`、`./proxy_providers/`），需要联网。
测试某个端口是否通：`curl.exe -I -x http://127.0.0.1:7897 https://www.gstatic.com/generate_204`。

## 自定义规则和分组

想改规则集、分组、规则或 DNS，把仓库里的 `config/` 复制到当前目录再修改，脚本会优先使用当前目录的 `config/`，不用动代码。

| 文件 | 内容 |
|---|---|
| `config/rulesets.yaml` | 规则集：名字 + URL |
| `config/groups.yaml` | 模板分组 |
| `config/rules.yaml` | 模板规则（自上而下，命中即停） |
| `config/base.yaml` | DNS、TUN、控制器等通用设置 |

怎么加规则集、加组、加规则，见 [template 模式](docs/03-template.md#5-怎么改模板)。

## 命令行参数

| 参数 | 作用 |
|---|---|
| `--yes` | 存在 `sources.yaml` 时直接复用，不询问 |
| `--sources 文件` | 指定订阅配置文件（默认 `sources.yaml`） |
| `--out 文件` | 输出路径（默认 `merged.yaml`） |
| `--listen 地址` | 监听地址（默认 `127.0.0.1`） |
| `--config-dir 目录` | 模板目录（默认优先当前目录的 `config/`） |
| `--refresh` | 强制重新下载订阅片段 |
| `--ua UA` | 下载订阅时使用的 User-Agent |
| `--probe-ua` | 探测本机 Clash 客户端的真实 UA 并保存 |

`--ua` 和 `--probe-ua` 只在 preserve 模式下载订阅时才有用，详见 [sources.yaml 与订阅片段](docs/04-snippets-and-import.md)。

## 文件说明

```
merge.py            主脚本
share_links.py      分享链接 -> 节点的转换器
config/             模板（规则集、分组、规则、DNS）
sources.yaml        你的来源和端口配置（运行后生成，含订阅 token，已在 .gitignore）
nodes.txt           你的分享链接（含密码，已在 .gitignore）
snippets/           下载或手动放置的订阅片段（已在 .gitignore）
merged.yaml         生成的配置
```

脚本输出前会自动检查：端口冲突、分组/节点重名、引用了不存在的规则集等会直接报错；未使用的规则集和分组只会警告。

---

## 更多文档与原理

遇到问题先看 [常见问题与排错](docs/05-troubleshooting.md)。想了解原理：

- [概念与原理](docs/01-concepts.md)：端口、`sub-rules`、`proxy-providers`、入口与 profile
- [template 模式](docs/03-template.md)：统一模板分组/规则（推荐），含共享组、仅默认入口的组、改模板的方法
- [preserve 模式](docs/02-preserve.md)：保留订阅自带的分组和规则
- [sources.yaml 与订阅片段](docs/04-snippets-and-import.md)：订阅下载、UA、分享链接、节点重名处理

两种 profile 的区别：

- **template**（推荐）：所有入口统一用 `config/` 里的模板分组和规则。
- **preserve**：保留订阅自带的分组和规则，只做命名空间化，需要 Clash YAML 格式的订阅片段。
