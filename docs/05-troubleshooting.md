<!-- docs/05-troubleshooting.md -->
# 常见问题与排错

← [返回 README](../README.md)  
← 上一篇：[准备订阅片段 YAML](04-snippets-and-import.md)

---

## 1) 端口连通性怎么测试？

### 是否监听
Windows：
- `netstat -ano | findstr :10001`

### TCP 能否连上
PowerShell：
- `Test-NetConnection 127.0.0.1 -Port 10001`

### 代理是否可用（推荐）
- `curl.exe -I -x http://127.0.0.1:10001 https://www.gstatic.com/generate_204`

---

## 2) 为什么 ping 出来是 198.18.x.x？

这通常是 fake-ip DNS 的表现：域名解析成保留测试网段的“假 IP”用于内部映射。
不要用 ping 判断代理是否正常；用 curl 走端口测更准。

---

## 3) preserve 模式下「组里节点变少/看不到一堆节点」正常吗？

正常。preserve 会对**非叶子组**删除「节点枚举」，只保留子组引用和 DIRECT/REJECT/PASS，
并在远程订阅场景下补 `use: [ns]` 作为节点入口。脚本运行时会打印提示。

详见：[preserve 模式：非叶子节点删除原理](02-preserve.md#4-非叶子节点删除原理)

---

## 4) template 模式下某些模板组没有节点？

只有 `config/groups.yaml` 里 `proxies` 含 `LEAF` 的组才会注入节点。没有 `LEAF` 的组只保留内置策略和组引用。

---

## 5) 订阅不是 Clash YAML，下载失败怎么办？

自动下载只支持 Clash YAML。在 Clash Verge Rev 添加订阅后，「编辑文件」复制内容保存为 `snippets/<ns>.yaml`，
脚本会直接使用该缓存。详见 [sources.yaml 与订阅片段](04-snippets-and-import.md)。

---

## 6) 开了 DoH 后所有域名都解析失败？

DoH 请求经 `🤖 AI服务` 组发出。若该组选了 `DIRECT` 或节点不通，就会解析失败。
先在面板里把该组切到可用节点；`proxy-server-nameserver` 保持直连的国内 DNS，不能走代理组。

---

## 7) 校验输出里的警告是什么意思？

| 警告 | 含义 | 要处理吗 |
|---|---|---|
| 规则集 X 已定义但未被任何规则使用 | preserve 订阅自带的规则集没被规则引用 | 一般可忽略 |
| 分组 X 未被任何规则或分组引用 | 这个组只能在面板里手动选 | 看你是否需要 |
| 规则的策略不存在 | 规则指向了找不到的组/节点（常见于 preserve 订阅里的嵌套规则） | 建议检查 |
| dns.nameserver 指向了不存在的分组 | `#组名` 写错，或默认入口名不对 | 要处理，否则 DoH 失败 |

**错误**（端口冲突、重名、引用不存在的规则集/provider）会直接退出，不生成文件。

---

## 8) 启动后 53 端口被占用 / 无法监听？

`dns.listen` 默认是 `127.0.0.1:53`。Windows 上 53 端口可能被系统服务占用，可在 `config/base.yaml` 里换成 `127.0.0.1:1053`；
开了 TUN 时 `dns-hijack` 会接管 DNS，一般不依赖 53 端口。

---

## 9) ping 域名拿不到真实 IP，想看真实地址怎么办？

fake-ip 下这是正常现象（见第 2 节）。排查时到 mihomo 控制器（`127.0.0.1:9090`）的连接面板看每条连接的真实目标，比 ping 准确。
