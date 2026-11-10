<!-- docs/04-snippets-and-import.md -->
# sources.yaml 与订阅片段

← [返回 README](../README.md)  
← 上一篇：[template 模式](03-template.md)  
→ 下一篇：[常见问题与排错](05-troubleshooting.md)

---

## 1) sources.yaml

分成「来源」和「入口」两部分，一个来源可以被多个入口引用：

```yaml
sources:
  - ns: sub1
    url: https://example.com/sub?token=xxx   # 远程订阅
  - ns: myvps
    url: local                               # 本地节点
    snippet: snippets/myvps.yaml             # local 时必填，含 proxies

entries:
  - name: main
    port: 7897
    sources: [sub1]
    profile: template
  - name: mix
    port: 7898
    sources: [sub1, myvps]   # 远程进 use，本地进 proxies，同一个组
    profile: template
  - name: ALL
    port: 7899
    sources: [sub1, myvps]
    profile: template
    default: true            # 它的规则同时作为全局 rules，DoH 也走它的组
```

没有这个文件时，脚本会交互询问并生成；有的话会询问是否复用。**文件里有订阅 token，已在 `.gitignore` 中，不要提交。**

## 2) 什么时候需要订阅片段（snippet）

| 场景 | 是否需要 |
|---|---|
| 远程订阅 + template | 不需要，节点由 mihomo 的 proxy-providers 自己拉取 |
| 远程订阅 + preserve | 需要，要读取订阅自带的分组和规则 |
| 本地节点（`local`） | 需要，含 `proxies:` |

## 3) 自动下载

preserve 模式下，远程订阅的片段会自动下载到 `snippets/<ns>.yaml`：

- 只支持 **Clash YAML**。Base64 / v2ray 链接列表会报错。
- 已有缓存时不重复下载，`--refresh` 强制更新；下载失败会回退到缓存并给出警告。
- 报错信息里的订阅链接会被隐藏，只保留域名，可以放心贴出来求助。
- 如果订阅商不返回 Clash 格式，可在 Clash Verge Rev 添加订阅后，「编辑文件」复制出来存成 `snippets/<ns>.yaml`。

### User-Agent（只在下载订阅时使用）

很多机场按 UA 决定返回什么格式，甚至直接拒绝。脚本下载订阅时发送的 UA 按以下优先级确定：

1. 命令行 `--ua`
2. `sources.yaml` 顶层的 `ua:`
3. 默认值 `clash-verge/v2.4.4`（Clash Verge 2.4.4 实测的 UA，格式为 `clash-verge/v<版本号>`）

**自动探测真实 UA**：
- 新建 `sources.yaml` 且有 preserve + 远程订阅时，脚本会问你是否探测。
- 下载失败时，交互模式下会提示“是否探测本机 Clash 客户端的 UA 后重试”。
- 也可以随时运行 `python merge.py --probe-ua`。

探测流程：脚本在 `127.0.0.1` 的空闲端口起一个临时服务，你在自己的 Clash 客户端里新建订阅，地址填脚本打印的那个，点更新，
脚本读到 UA 后保存到 `sources.yaml` 的 `ua:`，之后自动使用。只读取 `User-Agent` 这一个请求头，探测完可在客户端里删除测试订阅。

> template 模式下节点由 mihomo 自己拉取，不经过这个脚本，所以 UA 设置**不起作用**。

## 4) 本地节点片段示例

```yaml
proxies:
  - {name: hk, type: ss, server: 1.2.3.4, port: 8388, cipher: aes-128-gcm, password: xxx}
```

输出时节点名保持原样（`hk`）。

## 5) 节点重名怎么处理

**默认不给节点加前缀**，保持订阅里的原名。重名时按下面的规则处理：

| 情况 | 处理 |
|---|---|
| 本地节点与本地节点重名 | 先出现的保留原名，后面的改名为 `原名-来源名`（如 `hk-vps2`），并打印提示 |
| 本地节点与远程节点重名 | 保留远程，改本地节点（改名规则同上） |
| 远程与远程重名（节点不同） | **不处理** |

两点限制：
- 脚本在生成时只能看到**本地节点**，以及 preserve 模式下已下载的订阅片段里的节点。template 模式下的远程节点名要等 mihomo 拉取后才知道，所以本地节点和这类远程节点的重名**无法提前发现**，也就不会被改名。
- 远程与远程重名时，mihomo 里会出现两个同名节点，在面板里按名字选择可能有歧义。如果你的多个订阅确实有同名节点，给其中一个来源设 `prefix` 即可区分。

**可选的 `prefix`**（写在来源上，默认没有）：

```yaml
sources:
  - ns: sub1
    url: https://example.com/sub
    prefix: "A·"      # 节点显示为 A·香港01，会写成 provider 的 override.additional-prefix
```

> 本地节点重名只会影响 `merged.yaml` 里的名字；如果你在 `sources.yaml` 之外手动引用了某个本地节点名，注意它可能已被改名。
