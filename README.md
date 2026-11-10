<!-- README.md -->
# 多订阅多端口配置生成器（Clash Verge Rev + mihomo）

> 一个 mihomo 实例同时开多个本地端口；每个端口（入口）绑定自己的订阅、分组与规则。
> 一个组里可以同时放远程订阅节点（`use`）和手动节点（`proxies`）。

## 目录

- [概念与原理](docs/01-concepts.md)
- [preserve 模式：保留订阅原始 group/rules](docs/02-preserve.md)
- [template 模式：统一模板分组/规则](docs/03-template.md)
- [sources.yaml 与订阅片段](docs/04-snippets-and-import.md)
- [常见问题与排错](docs/05-troubleshooting.md)

---

## 快速开始

```bash
pip install ruamel.yaml requests
python merge.py
```

1. 第一次运行没有 `sources.yaml`，脚本会交互询问：来源（名字、订阅链接或 `local`）、端口、模式，并自动保存为 `sources.yaml`。
2. 之后再运行，会显示摘要并询问「是否复用」。回答 `n` 重新输入（旧文件备份为 `sources.yaml.bak`）。
3. 输出 `merged.yaml`，导入 Clash Verge Rev 使用。

常用参数：

| 参数 | 作用 |
|---|---|
| `--sources 文件` | 指定订阅配置文件（默认 `sources.yaml`） |
| `--yes` | 存在 `sources.yaml` 时直接复用，不询问 |
| `--refresh` | 强制重新下载订阅片段 |
| `--out 文件` | 输出路径（默认 `merged.yaml`） |
| `--listen 地址` | 监听地址（默认 `127.0.0.1`） |
| `--config-dir 目录` | 模板目录（默认 `config/`） |

---

## 导入使用

- **Clash Verge Rev**：新建一个本地配置，导入 `merged.yaml`，然后切换到它。
- **直接用 mihomo**：`mihomo -f merged.yaml -d <工作目录>`。

注意：配置里的 `./rules/`、`./proxy_providers/` 是相对 mihomo **工作目录**的路径，首次启动时会自动下载生成，需要联网。
启动后可用 `curl.exe -I -x http://127.0.0.1:7897 https://www.gstatic.com/generate_204` 测试某个端口是否通。

---

## 目录结构

```
config/
  base.yaml        dns / tun / controller（{DEFAULT} 会替换成默认入口名，用于 DoH 走的代理组）
  rulesets.yaml    规则集：每个只声明一次（名字 + URL）
  groups.yaml      模板分组（每个入口各一份，自动加 "入口名/" 前缀）
  rules.yaml       模板规则（自上而下，命中即停；策略写组名）
  sources.example.yaml
merge.py
sources.yaml       运行后生成，含订阅 token（已在 .gitignore）
snippets/          自动下载或手动放置的订阅片段（已在 .gitignore）
```

改规则集、分组、规则，只需要改 `config/` 下的文件，不用动 `merge.py`。

---

## 两种 profile

- **template**（推荐）：所有入口统一用 `config/` 里的模板分组/规则。
- **preserve**：保留订阅自带的分组和规则，只做命名空间化。需要 Clash YAML 格式的订阅片段（可自动下载）。

---

## 输出前校验

脚本会检查并报错：端口冲突、分组/节点重名、规则引用了不存在的规则集、`use` 引用了不存在的 provider。
会警告：已声明但未使用的规则集和分组、策略指向不存在的目标。
