<!-- README.md -->
# 多订阅多端口配置生成器（Clash Verge Rev + mihomo）

> 目标：用于合成订阅。一个 mihomo 实例同时开多个本地端口；每个端口可绑定不同订阅与规则；支持保留订阅原始分组，或统一使用内置模板分组/规则。

## 目录

- [概念与原理（新手必读）](docs/01-concepts.md)
- [模式 A：保留订阅原始 group/rules](docs/02-mode-a-preserve.md)
- [模式 B：内置模板分组/规则（订阅前缀 ns/…）](docs/03-mode-b-acl4ssr.md)
- [准备订阅片段 YAML & v2ray 链接导入流程](docs/04-snippets-and-import.md)
- [常见问题与排错](docs/05-troubleshooting.md)

---

## 快速开始（极简流程）

1. 为每个订阅准备一个 YAML 片段文件（snippet）
   - 远程订阅：先在 Clash Verge Rev 添加订阅，然后复制“编辑文件”的内容到本地 `snippets/<ns>.yaml`
   - local 节点：用 local 订阅文件维护 `proxies:`（见文档）
2. 安装前置软件包
```bash
pip install ruamel.yaml
```
3. 运行脚本
```bash
python merge.py
```
4.选择模式：
   - 模式 A：保留原始 group/rules
   - 模式 B：内置模板分组/规则

5.输入：
   - ns（订阅命名空间）、端口、订阅链接（或 local）、对应 snippet.yaml 路径

6.输出 `merged.yaml`，导入 Clash Verge Rev 使用

> 说明：脚本目前不会根据订阅链接自动下载并落盘为 YAML（原因与工作流见：[准备订阅片段](docs/04-snippets-and-import.md#为什么脚本不会直接下载订阅yaml)）。

---

## 我该选模式 A 还是模式 B？

- 订阅自带的分组/规则很复杂，你想尽量保留它的结构 → 选 [模式 A](docs/02-mode-a-preserve.md)
- 你只想统一一套分流逻辑，所有端口都按模板分组/规则 → 选 [模式 B](docs/03-mode-b-acl4ssr.md)
