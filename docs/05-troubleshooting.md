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

## 3) 模式 A 下“组里节点变少/看不到一堆节点”是正常的吗？

正常。模式 A 会对**非叶子组**删除“节点枚举”，只保留：
- 子组引用
- DIRECT/REJECT/PASS
并在发生删除时补 `ns/默认`，保证你仍然能选到该订阅的真实节点入口。

详见：[模式 A：非叶子节点删除原理](02-mode-a-preserve.md#4-非叶子节点删除原理你提到的核心点)

---

## 4) 为什么需要每个订阅都有 `ns/默认`？

- 作为订阅的“代表出口”，可被 `ALL/默认` 聚合
- 为模式 A 的非叶子组提供一个不会过期的“节点入口”
详见：[模式 A：为什么必须新增 ns/默认](02-mode-a-preserve.md#2-为什么必须新增-ns默认)

---

## 5) 脚本不自动下载订阅 YAML 怎么办？

推荐流程是：
- 在 Clash Verge Rev 添加订阅并更新
- 从“编辑文件”复制出 YAML 保存为 snippet 文件
详见：[准备订阅片段：为什么不直接下载](04-snippets-and-import.md#1-为什么脚本不会直接下载订阅yaml)
