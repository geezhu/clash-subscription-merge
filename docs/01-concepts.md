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
- `sub-rules`：可以为每个端口单独定义一套规则（比如 `rules_<ns>`、`template_<ns>`）

因此“多端口不同规则” = `listeners[].rule -> sub-rules.<name>`。

## 3. proxy-providers 与 local proxies

### 远程订阅（proxy-providers）
- 订阅链接交给 `proxy-providers` 管理：定时更新、健康检查
- 典型搭配：`override.additional-prefix: ns/` 给节点名加命名空间前缀，避免多订阅重名

### local 节点（proxies）
- local 不走 provider，节点写在 `proxies:` 里
- 一般也会给节点名加 `ns/` 前缀，保证全局唯一

## 4. proxy-groups：规则最终落点（policy）

rules 的最后会落到：
- DIRECT（直连）
- REJECT（拒绝）
- 某个策略组（proxy-group）
- 或某个具体节点

脚本的工作核心，就是把多个订阅的策略组“安全地合并”和“可维护地复用”。

## 5. 模式 A / 模式 B

- 模式 A：保留订阅原始 group/rules，只做命名空间化与“叶子/非叶子”改写。
- 模式 B：统一使用内置模板分组/规则（模板写在 `merge.py`，不依赖 `template.yaml`）。

## 6. 模板里的 LEAF 占位符

- `LEAF` 表示“该组需要注入节点”
- 远程订阅：该组会加入 `use: [ns]`
- 本地订阅：该组会加入本地 `proxies`

---

下一篇：
- [模式 A：保留订阅原始 group/rules](02-mode-a-preserve.md)
- [模式 B：内置模板分组/规则](03-mode-b-acl4ssr.md)
