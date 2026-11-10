<!-- docs/01-concepts.md -->
# 概念与原理（新手必读）

← [返回 README](../README.md)

## 1. 一个端口 = 一个“入站入口”

你的应用（浏览器/IDE/下载器）连接本地代理时，本质是连到一个端口（例如 7890、10001、10002）。
在 mihomo 里，端口通常由 `listeners` 定义：

- 一个 listener 对应一个端口（支持 mixed/http/socks 等）
- listener 可以绑定 `rule: <sub-rule-name>`，让这个端口使用独立的子规则集

## 2. rules 与 sub-rules：端口级规则隔离的核心

- 全局 `rules`：**没有绑定 listener.rule 的入站**会走全局 rules（常称“默认端口规则”）
- `sub-rules`：可以为每个端口单独定义一套规则（比如 `rules_<入口名>`）

因此“多端口不同规则” = `listeners[].rule -> sub-rules.<name>`。

## 3. proxy-providers 与 local proxies

### 远程订阅（proxy-providers）
- 订阅链接交给 `proxy-providers` 管理：定时更新、健康检查
- 默认不改节点名；需要时给来源加 `prefix`，会写成 `override.additional-prefix`，用来区分多个订阅里的同名节点

### local 节点（proxies）
- local 不走 provider，节点写在 `proxies:` 里
- 节点名保持原样；与已有节点重名时，本地节点会被自动改名（见 [节点重名](04-snippets-and-import.md#节点重名怎么处理)）

## 4. proxy-groups：规则最终落点（policy）

rules 的最后会落到：
- DIRECT（直连）
- REJECT（拒绝）
- 某个策略组（proxy-group）
- 或某个具体节点

脚本的工作核心，就是把多个订阅的策略组“安全地合并”和“可维护地复用”。

## 5. 入口（entry）与 profile

- 入口 = 一个端口 + 它引用的若干订阅/本地节点 + 一种 profile。
- `template`：统一使用 `config/` 里的模板分组/规则。
- `preserve`：保留订阅自带分组/规则，只做命名空间化与「叶子/非叶子」改写。

## 6. 模板里的 LEAF 占位符

- `LEAF` 表示「该组需要注入该入口的全部节点」
- 远程订阅：加入该组的 `use`
- 本地节点：加入该组的 `proxies`
- 同一个组里两者可以同时存在

---

下一篇：
- [preserve 模式](02-preserve.md)
- [template 模式](03-template.md)
