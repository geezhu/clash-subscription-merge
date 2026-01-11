#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a single mihomo/Clash config from multiple "sources" using an interactive CLI.

Each source requires:
- ns (subscription name / namespace / provider name)
- port (listener port)
- url (subscription URL; use "local" to indicate a local-only snippet, no proxy-providers)
- snippet yaml path (a local yaml file containing at least: proxy-groups + rules
  optional: rule-providers, proxies)

Core behavior:
- Non-local sources: create proxy-providers.<ns> with override.additional-prefix "<ns>/"
- Merge proxy-groups from snippets with namespace prefix "<ns>/"
- Leaf groups (no references to other groups) rewrite nodes list into use:[ns] + filter exact match
- Non-leaf groups remove all node names; keep only BUILTIN (DIRECT/REJECT/PASS/...) + group references
  and auto-fill with DIRECT if nothing meaningful remains
- Put snippet rules into sub-rules.rules_<ns> and bind listener.rule to that sub-rule set
- Namespace rule-providers keys as "<ns>__<name>" and rewrite RULE-SET references accordingly
- Rewrite rule policy field (last or second-last if "no-resolve") if it matches group/proxy names

Dependencies:
    pip install ruamel.yaml
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Set


from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.allow_unicode = True

BUILTINS: Set[str] = {"DIRECT", "REJECT", "PASS", "GLOBAL", "DNS"}
PROVIDER_PLACEHOLDERS: Set[str] = {"PROVIDER", "__PROVIDER__", "{PROVIDER}", "{provider}", "${PROVIDER}"}
LEAF_PLACEHOLDER = "LEAF"
LEAF_PLACEHOLDERS: Set[str] = {LEAF_PLACEHOLDER}

# Default base config used when --base is not provided.
DEFAULT_BASE_CONFIG: Dict[str, Any] = {
    "mode": "rule",
    "log-level": "info",
    "ipv6": False,
    "external-controller": "0.0.0.0:9090",
    "dns": {
        "enable": True,
        "listen": "0.0.0.0:53",
        "ipv6": False,
        "default-nameserver": [
            "223.5.5.5",
            "114.114.114.114",
        ],
        "nameserver": [
            "223.5.5.5",
            "114.114.114.114",
            "119.29.29.29",
            "180.76.76.76",
        ],
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter": [
            "*.lan",
            "*.localdomain",
            "*.example",
            "*.invalid",
            "*.localhost",
            "*.test",
            "*.local",
            "*.home.arpa",
            "router.asus.com",
            "localhost.sec.qq.com",
            "localhost.ptlogin2.qq.com",
            "+.msftconnecttest.com",
        ],
    },
    "tun": {
        "enable": True,
        "stack": "system",
        "auto-route": True,
        "auto-detect-interface": True,
        "dns-hijack": [
            "114.114.114.114",
            "180.76.76.76",
            "119.29.29.29",
            "223.5.5.5",
            "8.8.8.8",
            "8.8.4.4",
            "1.1.1.1",
            "1.0.0.1",
        ],
    },
}

TEMPLATE_LEAF_GROUPS: Set[str] = {"🚀 节点选择", "♻️ 自动选择"}
TEMPLATE_GROUPS: List[Dict[str, Any]] = [
    {
        "name": "🚀 节点选择",
        "type": "select",
        "proxies": [
            "♻️ 自动选择",
            "DIRECT",
            "LEAF",
        ],
    },
    {
        "name": "♻️ 自动选择",
        "type": "url-test",
        "proxies": [
            "LEAF",
        ],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
        "tolerance": 50,
    },
    {
        "name": "🌍 国外媒体",
        "type": "select",
        "proxies": [
            "🚀 节点选择",
            "♻️ 自动选择",
            "🎯 全球直连",
            "LEAF",
        ],
    },
    {
        "name": "📲 电报信息",
        "type": "select",
        "proxies": [
            "🚀 节点选择",
            "🎯 全球直连",
            "LEAF",
        ],
    },
    {
        "name": "Ⓜ️ 微软服务",
        "type": "select",
        "proxies": [
            "🎯 全球直连",
            "🚀 节点选择",
            "LEAF",
        ],
    },
    {
        "name": "🍎 苹果服务",
        "type": "select",
        "proxies": [
            "🚀 节点选择",
            "🎯 全球直连",
            "LEAF",
        ],
    },
    {
        "name": "🎯 全球直连",
        "type": "select",
        "proxies": [
            "DIRECT",
            "🚀 节点选择",
            "♻️ 自动选择",
        ],
    },
    {
        "name": "🛑 全球拦截",
        "type": "select",
        "proxies": [
            "REJECT",
            "DIRECT",
        ],
    },
    {
        "name": "🍃 应用净化",
        "type": "select",
        "proxies": [
            "REJECT",
            "DIRECT",
        ],
    },
    {
        "name": "🐟 漏网之鱼",
        "type": "select",
        "proxies": [
            "🚀 节点选择",
            "🎯 全球直连",
            "♻️ 自动选择",
            "LEAF",
        ],
    },
]
TEMPLATE_RULE_PROVIDERS: Dict[str, Any] = {
    "LocalAreaNetwork": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/LocalAreaNetwork.list",
        "path": "./rules/LocalAreaNetwork.yaml",
    },
    "BanAD": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/BanAD.list",
        "path": "./rules/BanAD.yaml",
    },
    "BanProgramAD": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/BanProgramAD.list",
        "path": "./rules/BanProgramAD.yaml",
    },
    "GoogleCN": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/GoogleCN.list",
        "path": "./rules/GoogleCN.yaml",
    },
    "SteamCN": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/Ruleset/SteamCN.list",
        "path": "./rules/SteamCN.yaml",
    },
    "Microsoft": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/Microsoft.list",
        "path": "./rules/Microsoft.yaml",
    },
    "Apple": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/Apple.list",
        "path": "./rules/Apple.yaml",
    },
    "ProxyMedia": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/ProxyMedia.list",
        "path": "./rules/ProxyMedia.yaml",
    },
    "Telegram": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/Telegram.list",
        "path": "./rules/Telegram.yaml",
    },
    "ProxyLite": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/ProxyLite.list",
        "path": "./rules/ProxyLite.yaml",
    },
    "ChinaDomain": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/ChinaDomain.list",
        "path": "./rules/ChinaDomain.yaml",
    },
    "ChinaCompanyIp": {
        "type": "http",
        "behavior": "classical",
        "url": "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/refs/heads/master/Clash/ChinaCompanyIp.list",
        "path": "./rules/ChinaCompanyIp.yaml",
    },
}
TEMPLATE_RULES: List[str] = [
    "RULE-SET,LocalAreaNetwork,🎯 全球直连",
    "RULE-SET,BanAD,🛑 全球拦截",
    "RULE-SET,BanProgramAD,🍃 应用净化",
    "RULE-SET,GoogleCN,🎯 全球直连",
    "RULE-SET,SteamCN,🎯 全球直连",
    "RULE-SET,Microsoft,Ⓜ️ 微软服务",
    "RULE-SET,Apple,🍎 苹果服务",
    "RULE-SET,ProxyMedia,🌍 国外媒体",
    "RULE-SET,Telegram,📲 电报信息",
    "RULE-SET,ProxyLite,🚀 节点选择",
    "RULE-SET,ChinaDomain,🎯 全球直连",
    "RULE-SET,ChinaCompanyIp,🎯 全球直连",
    "GEOIP,CN,🎯 全球直连",
    "MATCH,🐟 漏网之鱼",
]




# -----------------------------
# Data structures
# -----------------------------
@dataclass
class Source:
    ns: str
    port: int
    url: str  # "local" or a real subscription URL
    yaml_path: str


@dataclass
class Maps:
    group_map: Dict[str, str]     # raw group name -> ns/group
    proxy_map: Dict[str, str]     # raw proxy name -> ns/proxy (only for local proxies)
    ruleset_map: Dict[str, str]   # raw rule-provider key -> ns__key


# -----------------------------
# Helpers
# -----------------------------
def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """
    y/yes -> True, n/no -> False.
    Enter -> default.
    """
    suffix = " (Y/n): " if default else " (y/N): "
    while True:
        s = input(prompt + suffix).strip().lower()
        if not s:
            return default
        if s in {"y", "yes"}:
            return True
        if s in {"n", "no"}:
            return False
        print("请输入 y 或 n")


def load_template_parts() -> tuple[list[dict], list[Any], dict]:
    return (
        copy.deepcopy(TEMPLATE_GROUPS),
        copy.deepcopy(TEMPLATE_RULES),
        copy.deepcopy(TEMPLATE_RULE_PROVIDERS),
    )


def merge_rule_providers(dst: dict, src: dict) -> None:
    if not isinstance(dst, dict):
        raise TypeError('cfg["rule-providers"] must be a dict')
    for k, v in src.items():
        if k not in dst:
            dst[k] = v


def _dedup_str_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _builtin_list_from_proxies(proxies: list[Any]) -> list[str]:
    builtins: list[str] = []
    for item in proxies:
        if isinstance(item, str) and item in BUILTINS:
            builtins.append(item)
    return _dedup_str_list(builtins)


def _analyze_group_proxies(
    proxies: list[Any],
    group_names: set[str],
) -> tuple[list[str], list[str], bool]:
    builtins: list[str] = []
    group_refs: list[str] = []
    needs_leaf = False
    for item in proxies:
        if not isinstance(item, str):
            continue
        if item in LEAF_PLACEHOLDERS:
            needs_leaf = True
            continue
        if item in BUILTINS:
            builtins.append(item)
        elif item in group_names:
            group_refs.append(item)
        else:
            needs_leaf = True
    return _dedup_str_list(builtins), _dedup_str_list(group_refs), needs_leaf


def build_template_maps(
    template_groups: list[dict],
    template_rule_providers: dict,
    ns: str,
) -> Maps:
    group_map: Dict[str, str] = {}
    for g in template_groups:
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]:
            group_map[g["name"]] = f"{ns}/{g['name']}"

    ruleset_map: Dict[str, str] = {}
    if isinstance(template_rule_providers, dict):
        for k in template_rule_providers.keys():
            ruleset_map[k] = f"{ns}__{k}"

    return Maps(group_map=group_map, proxy_map={}, ruleset_map=ruleset_map)


def sanitize_ns(ns: str) -> str:
    ns = ns.strip().replace("/", "_")
    if not ns:
        raise ValueError("订阅名字不能为空")
    return ns


def deep_merge(dst: Any, src: Any) -> Any:
    """Recursively merge dictionaries; append lists; overwrite scalars."""
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if k in dst:
                dst[k] = deep_merge(dst[k], v)
            else:
                dst[k] = v
        return dst
    if isinstance(dst, list) and isinstance(src, list):
        dst.extend(src)
        return dst
    return src


def load_yaml_file(path: str) -> Any:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到文件: {p}")
    return yaml.load(p.read_text(encoding="utf-8"))


def ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def prompt_int(prompt: str, min_v: int = 1, max_v: int = 65535) -> int:
    while True:
        s = input(prompt).strip()
        try:
            v = int(s)
            if v < min_v or v > max_v:
                raise ValueError
            return v
        except Exception:
            print(f"请输入 {min_v}~{max_v} 的整数")


def prompt_nonempty(prompt: str) -> str:
    while True:
        s = input(prompt).strip()
        if s:
            return s
        print("不能为空")


def prompt_sources() -> List[Source]:
    n = prompt_int("要合并多少个订阅/片段？: ", 1, 50)
    out: List[Source] = []
    for i in range(n):
        print(f"\n--- 第 {i + 1} 个 ---")
        ns = sanitize_ns(prompt_nonempty("订阅名字(用于命名空间/Provider名): "))
        port = prompt_int("端口(和默认的三个端口重复可能无法使用): ", 1, 65535)
        url = prompt_nonempty("订阅链接(填 local 表示本地，无需 proxy-providers): ")
        yaml_path = prompt_nonempty("对应的 YAML 片段路径(至少包含 proxy-groups + rules): ")
        out.append(Source(ns=ns, port=port, url=url, yaml_path=yaml_path))
    return out


def split_top_level_commas(line: str) -> List[str]:
    """
    Split a rule line by commas, but only at "top-level" commas.
    Avoid breaking AND/OR payloads like: AND,((IN-PORT,10001),(DOMAIN,...)),POLICY
    """
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    in_s = False
    in_d = False
    esc = False

    for ch in line:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\":
            buf.append(ch)
            esc = True
            continue

        if ch == "'" and not in_d:
            in_s = not in_s
            buf.append(ch)
            continue
        if ch == '"' and not in_s:
            in_d = not in_d
            buf.append(ch)
            continue

        if not in_s and not in_d:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue

        buf.append(ch)

    parts.append("".join(buf).strip())
    return parts


def regex_union_exact(names: List[str]) -> str:
    """Build ^(?:a|b|c)$ exact-match regex."""
    escaped = [re.escape(n) for n in names]
    return r"^(?:%s)$" % "|".join(escaped) if escaped else r"^$"


# -----------------------------
# Transform logic
# -----------------------------
def build_maps(snippet: Dict[str, Any], ns: str) -> Maps:
    groups = snippet.get("proxy-groups") or []
    proxies = snippet.get("proxies") or []
    rps = snippet.get("rule-providers") or {}

    group_map: Dict[str, str] = {}
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]:
            group_map[g["name"]] = f"{ns}/{g['name']}"

    proxy_map: Dict[str, str] = {}
    for p in proxies:
        if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]:
            proxy_map[p["name"]] = f"{ns}/{p['name']}"

    ruleset_map: Dict[str, str] = {}
    if isinstance(rps, dict):
        for k in rps.keys():
            ruleset_map[k] = f"{ns}__{k}"

    return Maps(group_map=group_map, proxy_map=proxy_map, ruleset_map=ruleset_map)


def raw_group_names(snippet: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    groups = snippet.get("proxy-groups") or []
    if isinstance(groups, list):
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]:
                names.add(g["name"])
    return names


def is_leaf_group(group: Dict[str, Any], raw_names: Set[str]) -> bool:
    """
    Leaf group: its 'proxies' list does NOT reference any other group name defined in this snippet.
    """
    proxies = group.get("proxies")
    if not isinstance(proxies, list):
        return True
    for x in proxies:
        if isinstance(x, str) and x in raw_names:
            return False
    return True


def namespace_rule_providers(rps: Dict[str, Any], maps: Maps) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (rps or {}).items():
        out[maps.ruleset_map.get(k, k)] = v
    return out


def rewrite_rule_line(line: str, maps: Maps) -> str:
    parts = split_top_level_commas(line)
    if len(parts) < 2:
        return line

    rule_type = parts[0].strip()

    # RULE-SET,SetName,Policy...
    if rule_type == "RULE-SET" and len(parts) >= 3:
        set_name = parts[1].strip()
        if set_name in maps.ruleset_map:
            parts[1] = maps.ruleset_map[set_name]

    # policy position: usually last; if last is no-resolve -> policy is second-last
    policy_idx = -1
    if parts[-1].strip() == "no-resolve" and len(parts) >= 3:
        policy_idx = -2

    policy = parts[policy_idx].strip()
    if policy in maps.group_map:
        parts[policy_idx] = maps.group_map[policy]
    elif policy in maps.proxy_map:
        parts[policy_idx] = maps.proxy_map[policy]

    return ",".join(parts)


def rewrite_group(
    g: Dict[str, Any],
    ns: str,
    maps: Maps,
    has_provider: bool,
    raw_names: Set[str],
) -> Dict[str, Any]:
    """
    - Rename group name -> ns/group
    - If local: rewrite references where possible; keep everything else
    - If provider:
        - leaf: nodes -> use:[ns] + filter exact match; keep only BUILTIN in proxies
        - non-leaf: drop node names; keep BUILTIN + group references; add use if nodes were dropped
    """
    g2 = dict(g)

    # rename group name
    old_name = g2.get("name")
    if isinstance(old_name, str) and old_name in maps.group_map:
        g2["name"] = maps.group_map[old_name]

    # normalize proxies list
    proxies_list = g2.get("proxies") if isinstance(g2.get("proxies"), list) else []

    # rewrite `use` placeholders (if any)
    if isinstance(g2.get("use"), list):
        new_use = []
        for u in g2["use"]:
            if isinstance(u, str) and u in PROVIDER_PLACEHOLDERS:
                new_use.append(ns)
            else:
                new_use.append(u)
        g2["use"] = new_use

    if not has_provider:
        # local snippet: rename group refs and local proxy refs; keep unknown tokens
        new_proxies: List[Any] = []
        for item in proxies_list:
            if not isinstance(item, str):
                new_proxies.append(item)
                continue
            if item in BUILTINS:
                new_proxies.append(item)
            elif item in maps.group_map:
                new_proxies.append(maps.group_map[item])
            elif item in maps.proxy_map:
                new_proxies.append(maps.proxy_map[item])
            else:
                new_proxies.append(item)
        if new_proxies:
            g2["proxies"] = new_proxies
        return g2

    # provider present
    leaf = is_leaf_group(g, raw_names)

    if leaf:
        # If snippet already uses use+filter without proxies, keep filter and force use:[ns]
        if not proxies_list:
            g2["use"] = [ns]
            # if no filter given, it means "all nodes in provider"
            # keep existing filter/exclude-filter if present
            return g2

        nodes: List[str] = []
        kept: List[str] = []

        for item in proxies_list:
            if not isinstance(item, str):
                continue
            if item in BUILTINS:
                kept.append(item)
                continue
            if item in raw_names:
                # should not happen for leaf, but handle anyway as group ref
                kept.append(maps.group_map.get(item, item))
                continue

            # treat as node
            node_name = item
            if not node_name.startswith(f"{ns}/"):
                node_name = f"{ns}/{node_name}"
            nodes.append(node_name)

        g2["use"] = [ns]
        g2["filter"] = regex_union_exact(nodes)

        if kept:
            g2["proxies"] = kept
        else:
            g2.pop("proxies", None)

        # leaf: keep exclude-filter if user provided (rare)
        return g2

    # non-leaf: keep only BUILTIN + group refs, and add use if we dropped any nodes
    kept2: List[str] = []
    dropped_any_node = False

    for item in proxies_list:
        if not isinstance(item, str):
            continue
        if item in BUILTINS:
            kept2.append(item)
        elif item in raw_names:
            kept2.append(maps.group_map.get(item, item))
        else:
            # node name (or unknown token) in a non-leaf group -> drop it
            dropped_any_node = True

    if dropped_any_node:
        use_list = g2.get("use") if isinstance(g2.get("use"), list) else []
        if ns not in use_list:
            use_list.append(ns)
        g2["use"] = use_list
    else:
        g2.pop("use", None)

    if kept2:
        g2["proxies"] = kept2
    else:
        if dropped_any_node:
            g2.pop("proxies", None)
        else:
            g2["proxies"] = ["DIRECT"]
    g2.pop("filter", None)
    g2.pop("exclude-filter", None)

    return g2


def rewrite_template_group(
    g: Dict[str, Any],
    *,
    ns: str,
    has_provider: bool,
    local_proxy_names: list[str],
    template_group_names: set[str],
    default_leaf_group_name: str,
) -> Dict[str, Any]:
    g2 = dict(g)
    old_name = g2.get("name")
    if isinstance(old_name, str) and old_name in template_group_names:
        g2["name"] = f"{ns}/{old_name}"

    proxies_list = g2.get("proxies") if isinstance(g2.get("proxies"), list) else []
    builtins, group_refs, needs_leaf = _analyze_group_proxies(proxies_list, template_group_names)
    is_leaf = isinstance(old_name, str) and old_name in TEMPLATE_LEAF_GROUPS

    if is_leaf:
        proxies_out = builtins + [f"{ns}/{name}" for name in group_refs]
        if has_provider:
            g2["use"] = [ns]
        else:
            proxies_out.extend(local_proxy_names)
            g2.pop("use", None)

        proxies_out = _dedup_str_list([p for p in proxies_out if isinstance(p, str)])
        if proxies_out:
            g2["proxies"] = proxies_out
        else:
            if has_provider:
                g2.pop("proxies", None)
            else:
                g2["proxies"] = ["DIRECT"]

        g2.pop("filter", None)
        g2.pop("exclude-filter", None)
        return g2

    proxies_out = builtins + [f"{ns}/{name}" for name in group_refs]
    if needs_leaf:
        if has_provider:
            g2["use"] = [ns]
        else:
            proxies_out.extend(local_proxy_names)
    else:
        g2.pop("use", None)

    proxies_out = _dedup_str_list([p for p in proxies_out if isinstance(p, str)])
    if proxies_out:
        g2["proxies"] = proxies_out
    else:
        if has_provider and needs_leaf:
            g2.pop("proxies", None)
        else:
            g2["proxies"] = [default_leaf_group_name]

    g2.pop("filter", None)
    g2.pop("exclude-filter", None)
    return g2


def apply_template_for_ns(
    cfg: dict,
    *,
    ns: str,
    has_provider: bool,
    local_proxy_names: list[str],
    template_groups: list[dict],
    template_rules: list[Any],
    template_group_names: set[str],
    rules_key: str,
) -> None:
    group_map = {name: f"{ns}/{name}" for name in template_group_names}
    maps = Maps(group_map=group_map, proxy_map={}, ruleset_map={})

    leaf_default_name = maps.group_map.get("🚀 节点选择", f"{ns}/🚀 节点选择")
    for g in template_groups:
        if isinstance(g, dict):
            cfg["proxy-groups"].append(
                rewrite_template_group(
                    g,
                    ns=ns,
                    has_provider=has_provider,
                    local_proxy_names=local_proxy_names,
                    template_group_names=template_group_names,
                    default_leaf_group_name=leaf_default_name,
                )
            )

    rewritten_rules = [rewrite_rule_line(str(r), maps) for r in template_rules]
    cfg.setdefault("sub-rules", {})
    cfg["sub-rules"][rules_key] = rewritten_rules


def ensure_all_template_groups(
    cfg: dict,
    *,
    template_groups: list[dict],
    template_group_names: set[str],
    remote_ns_list: list[str],
    local_proxy_names: list[str],
) -> None:
    groups = cfg.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    remote_use = _dedup_str_list([ns for ns in remote_ns_list if isinstance(ns, str) and ns])
    local_leaf = _dedup_str_list([p for p in local_proxy_names if isinstance(p, str) and p])

    def find(name: str) -> dict | None:
        for g in groups:
            if isinstance(g, dict) and g.get("name") == name:
                return g
        return None

    for g in template_groups:
        if not isinstance(g, dict):
            continue
        name = g.get("name")
        if not isinstance(name, str):
            continue

        g2 = dict(g)
        g2["name"] = f"ALL/{name}"

        proxies_list = g2.get("proxies") if isinstance(g2.get("proxies"), list) else []
        builtins, group_refs, needs_leaf = _analyze_group_proxies(proxies_list, template_group_names)

        proxies_out = builtins + [f"ALL/{ref}" for ref in group_refs]
        if needs_leaf:
            proxies_out.extend(local_leaf)

        proxies_out = _dedup_str_list([p for p in proxies_out if isinstance(p, str)])
        if proxies_out:
            g2["proxies"] = proxies_out
        else:
            g2.pop("proxies", None)

        if needs_leaf and remote_use:
            g2["use"] = list(remote_use)
        else:
            g2.pop("use", None)

        g2.pop("filter", None)
        g2.pop("exclude-filter", None)

        existing = find(g2["name"])
        if existing is None:
            groups.append(g2)
            continue

        for key, val in g2.items():
            if key in {"proxies", "use", "name"}:
                continue
            existing[key] = val

        if "proxies" in g2:
            required = g2["proxies"] if isinstance(g2.get("proxies"), list) else []
            existing_list = existing.get("proxies") if isinstance(existing.get("proxies"), list) else []
            extras = [x for x in existing_list if isinstance(x, str) and x not in required]
            existing["proxies"] = _dedup_str_list(required + extras)
        else:
            existing.pop("proxies", None)

        if "use" in g2:
            required_use = g2["use"] if isinstance(g2.get("use"), list) else []
            existing_use = existing.get("use") if isinstance(existing.get("use"), list) else []
            extras_use = [x for x in existing_use if isinstance(x, str) and x not in required_use]
            existing["use"] = _dedup_str_list(required_use + extras_use)
        else:
            existing.pop("use", None)


def apply_template_global(
    cfg: dict,
    *,
    template_groups: list[dict],
    template_rules: list[Any],
    template_rule_providers: dict,
    template_group_names: set[str],
    remote_ns_list: list[str],
    local_proxy_names: list[str],
) -> None:
    merge_rule_providers(cfg.setdefault("rule-providers", {}), template_rule_providers)
    ensure_all_template_groups(
        cfg,
        template_groups=template_groups,
        template_group_names=template_group_names,
        remote_ns_list=remote_ns_list,
        local_proxy_names=local_proxy_names,
    )

    all_maps = Maps(
        group_map={name: f"ALL/{name}" for name in template_group_names},
        proxy_map={},
        ruleset_map={},
    )
    cfg["rules"] = [rewrite_rule_line(str(r), all_maps) for r in template_rules]


# -----------------------------
# Build merged config
# -----------------------------
def build_config(
    sources: list,
    base: dict | None = None,
    listen_addr: str = "127.0.0.1",
    keep_original_groups_rules: bool = True,
) -> dict:
    merged: dict = base if isinstance(base, dict) else {}

    merged.setdefault("mode", "rule")
    merged.setdefault("proxy-providers", {})
    merged.setdefault("rule-providers", {})
    merged.setdefault("proxies", [])
    merged.setdefault("proxy-groups", [])
    merged.setdefault("listeners", [])
    merged.setdefault("sub-rules", {})
    merged.setdefault("rules", ["MATCH,DIRECT"])  # 默认端口用什么，你可后续再覆盖
    template_groups, template_rules, template_rule_providers = load_template_parts()
    template_group_names = {
        g.get("name") for g in template_groups if isinstance(g, dict) and isinstance(g.get("name"), str)
    }
    template_group_names = {n for n in template_group_names if isinstance(n, str)}

    remote_ns_list: list[str] = []
    local_proxy_names_all: list[str] = []

    for src in sources:
        snippet = load_yaml_file(src.yaml_path)
        if not isinstance(snippet, dict):
            raise ValueError(f"{src.yaml_path} 不是 YAML dict（请确保顶层是 key:value 形式）")

        is_local = src.url.strip().lower() == "local"
        has_provider = not is_local

        maps = build_maps(snippet, src.ns)
        raw_names = raw_group_names(snippet)

        # 1) provider (remote)
        if has_provider:
            pp = merged["proxy-providers"]
            if src.ns in pp:
                raise ValueError(f"provider 名冲突：{src.ns} 已存在")
            pp[src.ns] = {
                "type": "http",
                "url": src.url.strip(),
                "path": f"./proxy_providers/{src.ns}.yaml",
                "interval": 3600,
                "health-check": {
                    "enable": True,
                    "url": "https://www.gstatic.com/generate_204",
                    "interval": 300,
                    "lazy": True,
                    "expected-status": 204,
                },
                "override": {"additional-prefix": f"{src.ns}/"},
            }
            remote_ns_list.append(src.ns)

        # 2) import local proxies
        local_names: list[str] = []
        if is_local and isinstance(snippet.get("proxies"), list):
            for p in snippet["proxies"]:
                if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]:
                    p2 = dict(p)
                    new_name = maps.proxy_map.get(p["name"], f"{src.ns}/{p['name']}")
                    p2["name"] = new_name
                    merged["proxies"].append(p2)
                    local_names.append(new_name)
            local_proxy_names_all.extend(local_names)

        # 3) listener.rule
        if keep_original_groups_rules:
            # merge groups/rules from snippet
            groups = snippet.get("proxy-groups") or []
            if not isinstance(groups, list) or not groups:
                raise ValueError(f"{src.yaml_path} 缺少 proxy-groups（至少要有一个）")

            for g in groups:
                if isinstance(g, dict):
                    merged["proxy-groups"].append(
                        rewrite_group(
                            g=g,
                            ns=src.ns,
                            maps=maps,
                            has_provider=has_provider,
                            raw_names=raw_names,
                        )
                    )

            rules = snippet.get("rules") or []
            if not isinstance(rules, list):
                raise ValueError(f"{src.yaml_path} 的 rules 必须是列表")

            rules_key = f"rules_{src.ns}"
            if isinstance(snippet.get("rule-providers"), dict):
                merge_rule_providers(
                    merged["rule-providers"],
                    namespace_rule_providers(snippet.get("rule-providers") or {}, maps),
                )
            rewritten_rules = [rewrite_rule_line(str(r), maps) for r in rules]
            if not any(str(r).strip().startswith("MATCH,") for r in rewritten_rules):
                rewritten_rules.append("MATCH,DIRECT")
            merged["sub-rules"][rules_key] = rewritten_rules

            listener_rule = rules_key

        else:
            # ✅ 不保留：每个 ns 使用模板分组/规则
            listener_rule = f"template_{src.ns}"
            apply_template_for_ns(
                merged,
                ns=src.ns,
                has_provider=has_provider,
                local_proxy_names=local_names,
                template_groups=template_groups,
                template_rules=template_rules,
                template_group_names=template_group_names,
                rules_key=listener_rule,
            )

        merged["listeners"].append({
            "name": f"in-{src.ns}",
            "type": "mixed",
            "listen": listen_addr,
            "port": int(src.port),
            "udp": True,
            "rule": listener_rule,
        })

    apply_template_global(
        merged,
        template_groups=template_groups,
        template_rules=template_rules,
        template_rule_providers=template_rule_providers,
        template_group_names=template_group_names,
        remote_ns_list=remote_ns_list,
        local_proxy_names=local_proxy_names_all,
    )


    return merged


# -----------------------------
# Entrypoint
# -----------------------------
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="合并多订阅片段 -> mihomo config")
    ap.add_argument("--base", help="可选：基础配置 base.yaml（dns/tun 等通用设置；未提供时使用内置默认）")
    ap.add_argument("--out", default="merged.yaml", help="输出文件路径")
    ap.add_argument("--listen", default="127.0.0.1", help="listeners 监听地址（默认 127.0.0.1）")
    args = ap.parse_args()

    base = load_yaml_file(args.base) if args.base else copy.deepcopy(DEFAULT_BASE_CONFIG)
    keep_original = prompt_yes_no("是否保留原有订阅的 proxy-groups/rules（每个端口独立 sub-rules）？否则使用模板分组/规则简化", default=True)
    sources = prompt_sources()



    merged = build_config(
        sources=sources,
        base=base,
        listen_addr=args.listen,
        keep_original_groups_rules=keep_original,
    )

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        yaml.dump(merged, f)

    print(f"\n✅ 已生成：{out_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误：{e}")
        sys.exit(2)
