<!-- docs/04-snippets-and-import.md -->
# 准备订阅片段 YAML & v2ray 链接导入流程

← [返回 README](../README.md)  
← 上一篇：[模式 B：统一 ACL4SSR](03-mode-b-acl4ssr.md)  
→ 下一篇：[常见问题与排错](05-troubleshooting.md)

---

## 1) 为什么脚本不会直接下载订阅 YAML

当前脚本不会“根据订阅链接自动下载并解析为 yaml”，需要你先准备 snippet 文件。

原因（实用角度）：
- 订阅链接可能返回多种格式（Base64、Clash YAML、混合格式）
- 你在 Verge 里已经能完成订阅拉取与转换
- 对脚本来说，最重要的是拿到一份“可解析的 YAML”，用于：
  - 模式 A：保留 group/rules 时做命名空间重写、叶/非叶判断、use 化
  - 模式 B：local 节点导入（proxies）

推荐工作流：
- 先在 Clash Verge Rev 添加订阅
- 从“编辑文件”复制出 YAML，保存成 `snippets/<ns>.yaml`
- 脚本读取这份文件做合并/生成

---

## 2) 远程订阅（url 非 local）如何准备 snippet

1. Clash Verge Rev → 订阅 → 新建订阅（远程链接）
2. 订阅更新成功后，右键订阅 → 编辑文件
3. 全选复制保存到本地：`snippets/<ns>.yaml`

> 这份 snippet 对模式 A 很关键，因为其中包含订阅自带的 proxy-groups/rules，脚本才能进行“叶/非叶”改写。

---

## 3) v2ray/vmess/vless/hysteria 链接导入到 Clash（local 订阅）

当你只有节点链接、没有现成 Clash YAML，可以按以下流程：

### 3.1 获取默认模板
用 https://v2rayse.com/node-convert 生成/获取一个 Clash 默认模板（作为骨架）。

### 3.2 在 Clash Verge Rev 创建 local 订阅
Clash Verge Rev → 订阅 → 新建 → 类型 local → 输入名称 → 编辑文件  
把模板粘贴进去 → 保存。

### 3.3 追加节点
右键该 local 订阅 → 编辑节点 → 粘贴节点链接 → 添加后置代理节点  
进入 高级 → 拷贝 append 输出的节点片段  
回到“编辑文件”，把 append 内容合并进 `proxies:` 列表。

注意事项（非常重要）：
- 你新增/替换 `proxies` 后，要同步检查 `proxy-groups` 里引用的节点名是否存在
- 若 group 引用了旧节点名，需要更新 group 的 proxies 列表（或改为更通用的结构）

完成后，把该 local 订阅的“编辑文件”内容保存成 `snippets/<ns>.yaml`，脚本即可读取。

---

下一篇：
→ [常见问题与排错](05-troubleshooting.md)
