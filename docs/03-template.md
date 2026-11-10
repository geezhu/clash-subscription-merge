<!-- docs/03-template.md -->
# template 模式：统一模板分组/规则

← [返回 README](../README.md)  
← 上一篇：[preserve 模式](02-preserve.md)  
→ 下一篇：[sources.yaml 与订阅片段](04-snippets-and-import.md)

> 适合：不保留订阅自带的复杂 group/rules，所有端口统一使用 `config/` 里的模板（规则集来源于 ACL4SSR）。

---

## 1) 模板放在哪里

| 文件 | 内容 |
|---|---|
| `config/rulesets.yaml` | 规则集，只写名字和 URL；`type/behavior/format/path` 由 `defaults` 补全 |
| `config/groups.yaml` | 模板分组：每个入口 2 个（节点选择、全球直连）+ 仅默认入口 1 个（AI 服务）+ 全局共享 3 个（延迟测试、Google、广告拦截） |
| `config/rules.yaml` | 模板规则，**自上而下匹配，命中即停** |

## 2) 规则顺序的原则

范围小、更具体的放前面，范围大的放后面。例如 `Google.list` 含 `DOMAIN-KEYWORD,google`，
会匹配所有带 google 的域名，所以 `Gemini` 放它前面，`AI` 放它之后只处理非 Google 的 AI 服务。

当前顺序（节选）：`LocalAreaNetwork → 广告拦截 → Gemini → Google → AI → GoogleCN → SteamCN → ... → GEOIP,CN → MATCH`。
`GoogleCN` 排在 `Google` 后面，所以大部分条目会被 `Google` 先匹配，若 Google 更新/推送异常，把它挪到 `Google` 前面。

## 3) LEAF：节点怎么进组

`groups.yaml` 里 `proxies` 写 `LEAF` 表示「该入口的全部节点」：

- 入口引用的**远程订阅** → 写入该组的 `use`
- 入口引用的**本地节点** → 写入该组的 `proxies`
- 两者可以同时存在于同一个组

没有 `LEAF` 的组只保留内置策略（DIRECT/REJECT）和对其他组的引用。

`standalone: true` 的组（如 `♻️ 延迟测试`）不被其他组或规则引用，只用来在面板里查看各节点延迟，校验时不会警告“未被引用”，该字段不会写入输出。

**共享组（`scope: shared`）**：`♻️ 延迟测试`、`🔍 Google`、`🛑 广告拦截` 全局各只生成一份，名字不加入口前缀，`LEAF` 展开为**所有来源**的全部节点
（远程订阅进 `use`，本地节点进 `proxies`），与有没有 `ALL` 入口无关。共享组里可以引用内置策略、其他共享组，也可以引用**每个入口的组**：写 `🚀 节点选择` 会自动展开成所有 template 入口各自的那个组
（`main/🚀 节点选择`、`mix/🚀 节点选择`……），这样共享的 `🔍 Google` 就能“跟随某个端口的节点选择”。该字段不会写入输出。
`🔍 Google` 是共享的，所以**所有端口共用同一个选择**：在面板里切换它，所有入口的 Google/Gemini 流量一起换节点。
注意不要让入口组和共享组互相引用，否则会形成循环。

**仅默认入口的组（`scope: default`）**：`🤖 AI服务` 只给默认入口（`default: true`，通常是 `ALL`）生成，名字是 `ALL/🤖 AI服务`；
其他入口没有这个组，`rules.yaml` 里指向它的规则（如 `RULE-SET,AI,🤖 AI服务`）在其他入口中会被省略，这些流量按后面的规则走。
`base.yaml` 里的 DoH 也是经这个组发出的（`{DEFAULT}/🤖 AI服务`）。如果默认入口用的是 preserve 模式，就没有这个组，DoH 会因此失效，校验会给出警告。

## 4) 每个入口一份

每个入口生成 `入口名/组名` 的全套分组，以及 `sub-rules.rules_<入口名>`，监听端口通过 `listeners[].rule` 绑定。
标记 `default: true` 的入口，它的规则同时作为全局 `rules`。

## 5) 怎么改模板

都只改 `config/` 下的文件，不用动 `merge.py`。

**加一个规则集**（`rulesets.yaml`）：

```yaml
rulesets:
  MyList: https://example.com/my.list      # 其余字段自动补全
  Custom:                                   # 或单独覆盖字段
    url: https://example.com/c.yaml
    behavior: domain
    format: yaml
```

**加一个组**（`groups.yaml`）：

```yaml
  - name: 🎮 游戏
    type: select
    proxies: [🚀 节点选择, DIRECT, LEAF]
```

**加一条规则**（`rules.yaml`，位置决定优先级，策略写组名，不用加前缀）：

```yaml
  - RULE-SET,MyList,🎮 游戏
```

校验会兜底：`rules.yaml` 引用了没声明的规则集会直接报错；声明了但没用的规则集会被忽略并警告；没被引用的组会警告。

## 6) 优缺点

- 优点：规则体系统一，易维护；多个订阅之间组名互不污染。
- 缺点：订阅自带的细分策略组会被抛弃，需要的话改用 [preserve 模式](02-preserve.md)。
