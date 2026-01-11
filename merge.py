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
  and auto-fill with "<ns>/默认" if nothing meaningful remains
- Create per-source default group "<ns>/默认" using use:[ns]
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
from typing import Any, Dict, List, Optional, Set ,Tuple


from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.allow_unicode = True

BUILTINS: Set[str] = {"DIRECT", "REJECT", "PASS", "GLOBAL", "DNS"}
PROVIDER_PLACEHOLDERS: Set[str] = {"PROVIDER", "__PROVIDER__", "{PROVIDER}", "{provider}", "${PROVIDER}"}
ACL4SSR_RULESETS = {
    "LocalAreaNetwork": "Clash/LocalAreaNetwork.list",
    "BanAD": "Clash/BanAD.list",
    "BanProgramAD": "Clash/BanProgramAD.list",
    "GoogleCN": "Clash/GoogleCN.list",
    "SteamCN": "Clash/Ruleset/SteamCN.list",
    "Telegram": "Clash/Telegram.list",
    "ProxyMedia": "Clash/ProxyMedia.list",
    "ProxyLite": "Clash/ProxyLite.list",
    "ChinaDomain": "Clash/ChinaDomain.list",
    "ChinaCompanyIp": "Clash/ChinaCompanyIp.list",
}

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

def inject_acl4ssr_rule_providers(
    cfg: dict,
    repo_owner: str = "ACL4SSR",
    repo_name: str = "ACL4SSR",
    branch: str = "master",
    local_store_dir: str = "./rules/providers",
    interval: int = 86400,
) -> None:
    """
    Add ACL4SSR rule-providers into cfg["rule-providers"] (global, only once).
    Mihomo supports behavior=classical and format=text. :contentReference[oaicite:2]{index=2}
    """
    rp = cfg.setdefault("rule-providers", {})
    if not isinstance(rp, dict):
        raise TypeError('cfg["rule-providers"] must be a dict')

    base_url = f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{branch}"

    for name, rel_path in ACL4SSR_RULESETS.items():
        if name in rp:
            continue
        rp[name] = {
            "type": "http",
            "behavior": "classical",
            "format": "text",
            "url": f"{base_url}/{rel_path}",
            "path": f"{local_store_dir.rstrip('/')}/{name}.list",
            "interval": int(interval),
        }

def ensure_all_acl_groups(
    cfg: dict,
    *,
    all_default_group: str = "ALL/默认",
    all_direct_group: str = "ALL/直连",
    all_reject_group: str = "ALL/拦截",
) -> tuple[str, str, str]:
    """
    Ensure ALL-level ACL groups:
      - ALL/默认 must exist
      - ALL/直连: select [DIRECT, ALL/默认]
      - ALL/拦截: select [REJECT, DIRECT]

    No auto-renaming: if group exists, update it.
    """
    groups = cfg.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    def find(name: str):
        for g in groups:
            if isinstance(g, dict) and g.get("name") == name:
                return g
        return None

    if find(all_default_group) is None:
        raise ValueError(f"缺少 {all_default_group}，请先 ensure_all_group() 再调用 ensure_all_acl_groups()")

    def upsert_select(name: str, required_front: list[str]) -> None:
        g = find(name)
        if g is None:
            groups.append({"name": name, "type": "select", "proxies": list(required_front)})
            return

        g["type"] = "select"
        proxies = g.get("proxies")
        if not isinstance(proxies, list):
            proxies = []

        # force required_front at beginning, keep others after
        seen = set()
        new_list = []
        for x in required_front:
            if x not in seen:
                new_list.append(x)
                seen.add(x)
        for x in proxies:
            if isinstance(x, str) and x not in seen:
                new_list.append(x)
                seen.add(x)

        g["proxies"] = new_list

    upsert_select(all_direct_group, ["DIRECT", all_default_group])
    upsert_select(all_reject_group, ["REJECT", "DIRECT"])

    return all_default_group, all_direct_group, all_reject_group

def build_acl4ssr_rules_default_port(
    all_default_group: str,
    all_direct_group: str,
    all_reject_group: str,
) -> list[str]:
    return [
        f"RULE-SET,LocalAreaNetwork,{all_direct_group}",
        f"RULE-SET,BanAD,{all_reject_group}",
        f"RULE-SET,BanProgramAD,{all_reject_group}",
        f"RULE-SET,GoogleCN,{all_direct_group}",
        f"RULE-SET,SteamCN,{all_direct_group}",
        f"RULE-SET,Telegram,{all_default_group}",
        f"RULE-SET,ProxyMedia,{all_default_group}",
        f"RULE-SET,ProxyLite,{all_default_group}",
        f"RULE-SET,ChinaDomain,{all_direct_group}",
        f"RULE-SET,ChinaCompanyIp,{all_direct_group}",
        f"GEOIP,CN,{all_direct_group}",
        f"MATCH,{all_default_group}",
    ]


def ensure_ns_acl_groups(
    cfg: dict,
    ns: str,
    default_group_name: str,  # usually f"{ns}/默认"
    direct_suffix: str = "直连",
    reject_suffix: str = "拦截",
) -> tuple[str, str, str]:
    """
    Ensure per-namespace ACL groups (namespaced by subscription prefix):
      - <ns>/默认          (already ensured elsewhere)
      - <ns>/直连   select [DIRECT, <ns>/默认]
      - <ns>/拦截   select [REJECT, DIRECT]
    """
    groups = cfg.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    def find(name: str):
        for g in groups:
            if isinstance(g, dict) and g.get("name") == name:
                return g
        return None

    if find(default_group_name) is None:
        raise ValueError(f"缺少默认组：{default_group_name}（请先 ensure_ns_default_group）")

    direct_name = f"{ns}/{direct_suffix}"
    reject_name = f"{ns}/{reject_suffix}"

    def upsert_select(name: str, required_front: list[str]) -> None:
        g = find(name)
        if g is None:
            groups.append({"name": name, "type": "select", "proxies": list(required_front)})
            return

        g["type"] = "select"
        proxies = g.get("proxies")
        if not isinstance(proxies, list):
            proxies = []

        # force required_front at beginning, keep others after
        seen = set()
        new_list = []
        for x in required_front:
            if x not in seen:
                new_list.append(x)
                seen.add(x)
        for x in proxies:
            if isinstance(x, str) and x not in seen:
                new_list.append(x)
                seen.add(x)
        g["proxies"] = new_list

    upsert_select(direct_name, ["DIRECT", default_group_name])
    upsert_select(reject_name, ["REJECT", "DIRECT"])

    return default_group_name, direct_name, reject_name

def _get_group_names(cfg: dict) -> set[str]:
    names: set[str] = set()
    for g in cfg.get("proxy-groups", []) or []:
        if isinstance(g, dict) and isinstance(g.get("name"), str):
            names.add(g["name"])
    return names


def _ensure_unique_name(existing: set[str], base: str) -> str:
    if base not in existing:
        existing.add(base)
        return base
    # 避免撞订阅自带 group 名
    i = 1
    while f"{base}({i})" in existing:
        i += 1
    name = f"{base}({i})"
    existing.add(name)
    return name


def ensure_default_port_three_groups(
    cfg: dict,
    *,
    default_group: str = "ALL/默认",
    direct_group: str = "直连",
    reject_group: str = "拦截",
) -> tuple[str, str, str]:
    """
    Ensure 3 groups exist for ACL4SSR rules (GLOBAL, shared by all ports):

      - default_group: MUST exist (ALL/默认). If missing, raises.
      - direct_group: select [DIRECT, default_group]
      - reject_group: select [REJECT, DIRECT]

    IMPORTANT: No auto-renaming. If a group already exists with the same name,
    update its proxies to contain the required items (and keep any extra items).
    """
    groups = cfg.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    # Find group by name
    def find_group(name: str) -> dict | None:
        for g in groups:
            if isinstance(g, dict) and g.get("name") == name:
                return g
        return None

    if find_group(default_group) is None:
        raise ValueError(
            f"默认组 {default_group} 不存在。请先生成 ALL/默认（ensure_all_group）再调用。"
        )

    def upsert_select_group(name: str, required_front: list[str]) -> None:
        """
        Create or update a select group:
        - ensure type=select
        - ensure required items are present and placed in front (preserve others after)
        """
        g = find_group(name)
        if g is None:
            groups.append({
                "name": name,
                "type": "select",
                "proxies": list(required_front),
            })
            return

        # update existing
        g["type"] = "select"
        proxies = g.get("proxies")
        if not isinstance(proxies, list):
            proxies = []

        # de-dup keep order but force required_front at beginning
        seen = set()
        new_list = []
        for x in required_front:
            if x not in seen:
                new_list.append(x)
                seen.add(x)
        for x in proxies:
            if isinstance(x, str) and x not in seen:
                new_list.append(x)
                seen.add(x)

        g["proxies"] = new_list

    upsert_select_group(direct_group, ["DIRECT", default_group])
    upsert_select_group(reject_group, ["REJECT", "DIRECT"])

    return default_group, direct_group, reject_group

def set_default_port_rules_acl4ssr(
    cfg: dict,
    *,
    default_group: str,
    direct_group: str,
    reject_group: str,
) -> None:
    """
    Global rules (used by default port) based on ACL4SSR list.
    Only uses 3 groups: default_group / direct_group / reject_group.
    """
    cfg["rules"] = [
        f"RULE-SET,LocalAreaNetwork,{direct_group}",
        f"RULE-SET,BanAD,{reject_group}",
        f"RULE-SET,BanProgramAD,{reject_group}",
        f"RULE-SET,GoogleCN,{direct_group}",
        f"RULE-SET,SteamCN,{direct_group}",
        f"RULE-SET,Telegram,{default_group}",
        f"RULE-SET,ProxyMedia,{default_group}",
        f"RULE-SET,ProxyLite,{default_group}",
        f"RULE-SET,ChinaDomain,{direct_group}",
        f"RULE-SET,ChinaCompanyIp,{direct_group}",
        f"GEOIP,CN,{direct_group}",
        f"MATCH,{default_group}",
    ]

def build_acl4ssr_rules(default_group: str, direct_group: str, reject_group: str) -> list[str]:
    return [
        f"RULE-SET,LocalAreaNetwork,{direct_group}",
        f"RULE-SET,BanAD,{reject_group}",
        f"RULE-SET,BanProgramAD,{reject_group}",
        f"RULE-SET,GoogleCN,{direct_group}",
        f"RULE-SET,SteamCN,{direct_group}",
        f"RULE-SET,Telegram,{default_group}",
        f"RULE-SET,ProxyMedia,{default_group}",
        f"RULE-SET,ProxyLite,{default_group}",
        f"RULE-SET,ChinaDomain,{direct_group}",
        f"RULE-SET,ChinaCompanyIp,{direct_group}",
        f"GEOIP,CN,{direct_group}",
        f"MATCH,{default_group}",
    ]


def apply_acl4ssr(
    cfg: dict,
    *,
    all_proxy_group_name: str = "ALL/默认",
    direct_group_name: str = "直连",
    reject_group_name: str = "拦截",
    sub_rule_name: str | None = None,
    set_global_rules: bool = True,
) -> tuple[str, str, str]:
    """
    Inject ACL4SSR rule-providers, ensure 3 groups (NO auto-renaming),
    then write ACL4SSR rules to global rules and optionally to sub-rules[sub_rule_name].
    """
    inject_acl4ssr_rule_providers(cfg)

    default_group, direct_group, reject_group = ensure_default_port_three_groups(
        cfg,
        default_group=all_proxy_group_name,
        direct_group=direct_group_name,
        reject_group=reject_group_name,
    )

    rules_list = build_acl4ssr_rules(default_group, direct_group, reject_group)

    if set_global_rules:
        cfg["rules"] = list(rules_list)

    if sub_rule_name:
        cfg.setdefault("sub-rules", {})
        cfg["sub-rules"][sub_rule_name] = list(rules_list)

    return default_group, direct_group, reject_group

def build_acl4ssr_rules_for_ns(default_group: str, direct_group: str, reject_group: str) -> list[str]:
    # RULE-SET / MATCH 是 mihomo 的标准规则类型。:contentReference[oaicite:3]{index=3}
    return [
        f"RULE-SET,LocalAreaNetwork,{direct_group}",
        f"RULE-SET,BanAD,{reject_group}",
        f"RULE-SET,BanProgramAD,{reject_group}",
        f"RULE-SET,GoogleCN,{direct_group}",
        f"RULE-SET,SteamCN,{direct_group}",
        f"RULE-SET,Telegram,{default_group}",
        f"RULE-SET,ProxyMedia,{default_group}",
        f"RULE-SET,ProxyLite,{default_group}",
        f"RULE-SET,ChinaDomain,{direct_group}",
        f"RULE-SET,ChinaCompanyIp,{direct_group}",
        f"GEOIP,CN,{direct_group}",
        f"MATCH,{default_group}",
    ]


def apply_acl4ssr_for_ns(
    cfg: dict,
    ns: str,
    default_group_name: str,
    sub_rule_name: str,
    direct_suffix: str = "直连",
    reject_suffix: str = "拦截",
) -> None:
    """
    Ensure providers + per-ns groups, then write cfg["sub-rules"][sub_rule_name] for this ns.
    """
    inject_acl4ssr_rule_providers(cfg)

    default_g, direct_g, reject_g = ensure_ns_acl_groups(
        cfg,
        ns=ns,
        default_group_name=default_group_name,
        direct_suffix=direct_suffix,
        reject_suffix=reject_suffix,
    )

    rules_list = build_acl4ssr_rules_for_ns(default_g, direct_g, reject_g)

    sr = cfg.setdefault("sub-rules", {})
    if not isinstance(sr, dict):
        raise TypeError('cfg["sub-rules"] must be a dict')
    sr[sub_rule_name] = list(rules_list)


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


def ensure_default_group(merged: Dict[str, Any], ns: str) -> str:
    """
    Ensure per-provider default group exists: <ns>/默认
    """
    default_name = f"{ns}/默认"
    for g in merged.get("proxy-groups", []):
        if isinstance(g, dict) and g.get("name") == default_name:
            return default_name

    merged.setdefault("proxy-groups", []).append({
        "name": default_name,
        "type": "select",
        "use": [ns],
        "proxies": ["DIRECT"],  # optional
    })
    return default_name

def ensure_all_group(
    merged: dict,
    default_groups: list[str],
    all_group_name: str = "ALL/默认",
    add_direct: bool = True,
) -> str:
    """
    ALL/默认 = select [<every ns>/默认 ...] (+ DIRECT optional)
    """
    groups = merged.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    # de-dup keep order
    seen = set()
    proxies = []
    for g in default_groups:
        if isinstance(g, str) and g and g not in seen:
            proxies.append(g)
            seen.add(g)

    if add_direct and "DIRECT" not in seen:
        proxies.append("DIRECT")

    if not proxies:
        proxies = ["DIRECT"]

    for g in groups:
        if isinstance(g, dict) and g.get("name") == all_group_name:
            return all_group_name

    groups.append({"name": all_group_name, "type": "select", "proxies": proxies})
    return all_group_name

def ensure_ns_default_group(
    cfg: dict,
    *,
    ns: str,
    is_local: bool,
    local_proxy_names: list[str] | None = None,
    add_direct_for_local: bool = True,
) -> str:
    """
    Ensure per-namespace default group: <ns>/默认

    - remote (not local): type select, use [ns]
    - local: type select, proxies [<ns>/node1, <ns>/node2 ...] (+ DIRECT optional)

    Returns the group name (<ns>/默认).
    """
    name = f"{ns}/默认"

    groups = cfg.setdefault("proxy-groups", [])
    if not isinstance(groups, list):
        raise TypeError('cfg["proxy-groups"] must be a list')

    # already exists
    for g in groups:
        if isinstance(g, dict) and g.get("name") == name:
            return name

    if not is_local:
        groups.append({
            "name": name,
            "type": "select",
            "use": [ns],
            # 不需要 PASS；ALL/默认 就是默认
            # 也不强塞 DIRECT，避免“默认代理组”被误用成直连入口
        })
        return name

    # local
    members = []
    for p in (local_proxy_names or []):
        if isinstance(p, str) and p:
            members.append(p)

    if add_direct_for_local:
        members.append("DIRECT")

    if not members:
        # local 但没给 proxies，至少保证可用
        members = ["DIRECT"]

    groups.append({
        "name": name,
        "type": "select",
        "proxies": members,
    })
    return name


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
    default_group_name: str
) -> Dict[str, Any]:
    """
    - Rename group name -> ns/group
    - If local: rewrite references where possible; keep everything else
    - If provider:
        - leaf: nodes -> use:[ns] + filter exact match; keep only BUILTIN in proxies
        - non-leaf: drop node names; keep BUILTIN + group references; auto insert default group if needed
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

    # non-leaf: keep only BUILTIN + group refs, and add default group if we dropped any nodes
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

    # ✅ 如果曾经删除过节点枚举，就补默认组（避免“节点入口”丢失）
    if dropped_any_node and default_group_name not in kept2:
        # 你想它排前面就 insert(0)，想排后面就 append
        kept2.insert(0, default_group_name)

    # 兜底：如果最后还是空（极端情况），至少保留默认组
    if not kept2:
        kept2 = [default_group_name]

    g2["proxies"] = kept2

    # 非叶子：清掉 use/filter（避免非叶子也 use）
    g2.pop("use", None)
    g2.pop("filter", None)
    g2.pop("exclude-filter", None)

    return g2


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

    ns_default_groups: list[str] = []

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

        # 2) import local proxies (for local ns/默认)
        local_names: list[str] = []
        if is_local and isinstance(snippet.get("proxies"), list):
            for p in snippet["proxies"]:
                if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]:
                    p2 = dict(p)
                    new_name = maps.proxy_map.get(p["name"], f"{src.ns}/{p['name']}")
                    p2["name"] = new_name
                    merged["proxies"].append(p2)
                    local_names.append(new_name)

        # 3) ensure per-ns default group (remote uses use, local uses proxies)
        ns_default = ensure_ns_default_group(
            merged,
            ns=src.ns,
            is_local=is_local,
            local_proxy_names=local_names,
            add_direct_for_local=True,
        )
        ns_default_groups.append(ns_default)

        # 4) listener.rule
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
                            default_group_name=ns_default,  # 非叶子补默认组用 ns/默认
                        )
                    )

            rules = snippet.get("rules") or []
            if not isinstance(rules, list):
                raise ValueError(f"{src.yaml_path} 的 rules 必须是列表")

            rules_key = f"rules_{src.ns}"
            rewritten_rules = [rewrite_rule_line(str(r), maps) for r in rules]
            if not any(str(r).strip().startswith("MATCH,") for r in rewritten_rules):
                rewritten_rules.append(f"MATCH,{ns_default}" if has_provider else "MATCH,DIRECT")
            merged["sub-rules"][rules_key] = rewritten_rules

            listener_rule = rules_key

        else:
            # ✅ 不保留：每个 ns 一个 acl4ssr_<ns> 子规则（订阅前缀三组）
            listener_rule = f"acl4ssr_{src.ns}"
            apply_acl4ssr_for_ns(
                merged,
                ns=src.ns,
                default_group_name=ns_default,
                sub_rule_name=listener_rule,
            )

        merged["listeners"].append({
            "name": f"in-{src.ns}",
            "type": "mixed",
            "listen": listen_addr,
            "port": int(src.port),
            "udp": True,
            "rule": listener_rule,
        })

    # 5) ALL/默认 聚合：放所有 ns/默认（包括 local/remote）
    all_group_name = ensure_all_group(
        merged,
        default_groups=ns_default_groups,
        all_group_name="ALL/默认",
        add_direct=True,
    )

    # ✅ 6) 再补齐 ALL 级直连/拦截（你说的“丢失”就在这里补）
    all_default, all_direct, all_reject = ensure_all_acl_groups(
        merged,
        all_default_group=all_group_name,
        all_direct_group="ALL/直连",
        all_reject_group="ALL/拦截",
    )

    # ✅ 7) 默认端口（全局 rules）用 ACL4SSR（指向 ALL 级三组）
    #    这不会影响订阅端口，因为每个 listener 都绑定了自己的 sub-rules。
    inject_acl4ssr_rule_providers(merged)
    merged["rules"] = build_acl4ssr_rules_default_port(all_default, all_direct, all_reject)


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
    keep_original = prompt_yes_no("是否保留原有订阅的 proxy-groups/rules（每个端口独立 sub-rules）？否则使用ACL4SSR简化", default=True)
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
