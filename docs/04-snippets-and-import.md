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

preserve 模式下，远程订阅的片段会自动下载到 `snippets/<ns>.yaml`（User-Agent 为 `clash.meta`）：

- 只支持 **Clash YAML**。Base64 / v2ray 链接列表会报错。
- 已有缓存时不重复下载，`--refresh` 强制更新；下载失败会回退到缓存并给出警告。
- 如果订阅商不返回 Clash 格式，可在 Clash Verge Rev 添加订阅后，「编辑文件」复制出来存成 `snippets/<ns>.yaml`。

## 4) 本地节点片段示例

```yaml
proxies:
  - {name: hk, type: ss, server: 1.2.3.4, port: 8388, cipher: aes-128-gcm, password: xxx}
```

输出时节点名会加上来源前缀，变成 `myvps/hk`。
