#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build one mihomo/Clash config that exposes several local ports, each bound to its own
groups/rules.

Data lives in config/ (see README):
    base.yaml       dns / tun / controller ...   ("{DEFAULT}" is replaced by the default entry name)
    rulesets.yaml   rule-provider declarations (declared once)
    groups.yaml     template proxy-groups (one copy per entry; "LEAF" = all nodes of the entry)
    rules.yaml      template rules (policy = group name, prefixed automatically)
User input lives in sources.yaml (generated interactively if missing, reused on request):
    sources:  ns / url (or "local") / snippet
    entries:  name / port / sources[] / profile (template | preserve) / default

A group can mix remote subscriptions (written to `use`) and local nodes (written to `proxies`).

Dependencies:
    pip install ruamel.yaml requests
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.allow_unicode = True
yaml.width = 4096  # never wrap long URLs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # so `share_links` is importable however the script is launched
from share_links import parse_links_text  # noqa: E402
BUILTINS: Set[str] = {"DIRECT", "REJECT", "PASS", "GLOBAL", "DNS"}
PROVIDER_PLACEHOLDERS: Set[str] = {"PROVIDER", "__PROVIDER__", "{PROVIDER}", "{provider}", "${PROVIDER}"}
LEAF = "LEAF"
DEFAULT_PLACEHOLDER = "{DEFAULT}"
PROFILES = ("template", "preserve")
SHARED = "shared"  # groups.yaml `scope: shared`: one global copy over all sources, no entry prefix
DEFAULT_ONLY = "default"  # groups.yaml `scope: default`: only the default entry (e.g. ALL) gets this group
SNIPPET_UA = "clash-verge/v2.4.4"  # 默认伪装成 Clash Verge；可用 --ua 覆盖


# -----------------------------
# Data structures
# -----------------------------
@dataclass
class Source:
    ns: str
    url: str  # "local" or a subscription URL
    snippet: Optional[str] = None
    prefix: str = ""  # optional node-name prefix (default: none, keep original names)
    links: Optional[str] = None  # local source: path of a file with share links (one per line)

    @property
    def is_local(self) -> bool:
        return self.url.strip().lower() == "local"


@dataclass
class Entry:
    name: str
    port: int
    sources: List[str]
    profile: str = "template"
    default: bool = False


@dataclass
class Templates:
    groups: List[dict]
    rules: List[str]
    rulesets: Dict[str, dict]


@dataclass
class SourceInfo:
    has_provider: bool
    prefix: str = ""
    proxies: List[dict] = field(default_factory=list)  # raw local proxies (yaml snippet + share links)
    local_names: List[str] = field(default_factory=list)
    name_map: Dict[str, str] = field(default_factory=dict)  # snippet node name -> final node name
    snippet: Optional[dict] = None


@dataclass
class Maps:
    group_map: Dict[str, str]    # raw group name -> prefixed group name
    proxy_map: Dict[str, str]    # raw local proxy name -> ns/proxy
    ruleset_map: Dict[str, str]  # raw rule-provider key -> prefixed key


# -----------------------------
# Generic helpers
# -----------------------------
def plain(x: Any) -> Any:
    """Convert ruamel containers/scalars into plain python objects."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [plain(v) for v in x]
    if isinstance(x, str):
        return str(x)
    return x


def load_yaml_file(path: Path | str) -> Any:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到文件: {p}")
    return plain(yaml.load(p.read_text(encoding="utf-8")))


def dump_yaml_file(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)


def dedup(items: List[Any]) -> List[Any]:
    seen: set = set()
    out: List[Any] = []
    for it in items:
        key = it if isinstance(it, str) else id(it)
        if key not in seen:
            out.append(it)
            seen.add(key)
    return out


def unique_name(base: str, suffix: str, taken: Set[str]) -> str:
    """base-suffix, base-suffix2, ... until it is not in `taken`."""
    cand = f"{base}-{suffix}"
    i = 2
    while cand in taken:
        cand = f"{base}-{suffix}{i}"
        i += 1
    return cand


def sanitize_name(name: str, what: str = "名字") -> str:
    name = str(name).strip().replace("/", "_")
    if not name:
        raise ValueError(f"{what}不能为空")
    return name


def substitute(x: Any, old: str, new: str) -> Any:
    if isinstance(x, dict):
        return {k: substitute(v, old, new) for k, v in x.items()}
    if isinstance(x, list):
        return [substitute(v, old, new) for v in x]
    if isinstance(x, str):
        return x.replace(old, new)
    return x


def split_top_level_commas(line: str) -> List[str]:
    """Split a rule line on top-level commas only (AND/OR payloads keep their inner commas)."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    in_s = in_d = esc = False
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


def policy_index(parts: List[str]) -> int:
    """Index of the policy field: last, or second-last when followed by no-resolve."""
    if parts[-1].strip() == "no-resolve" and len(parts) >= 3:
        return -2
    return -1


def regex_union_exact(names: List[str]) -> str:
    escaped = [re.escape(n) for n in names]
    return r"^(?:%s)$" % "|".join(escaped) if escaped else r"^$"


# -----------------------------
# Prompts
# -----------------------------
def prompt_yes_no(prompt: str, default: bool = True) -> bool:
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


def prompt_profile() -> str:
    while True:
        s = input("分组/规则模式 [1=模板(推荐) 2=保留订阅原分组/规则] (默认 1): ").strip()
        if s in {"", "1"}:
            return "template"
        if s == "2":
            return "preserve"
        print("请输入 1 或 2")


def prompt_local_kind() -> str:
    while True:
        s = input("本地节点的来源 [1=分享链接文件(nodes.txt，推荐) 2=已有 YAML 片段] (默认 1): ").strip()
        if s in {"", "1"}:
            return "links"
        if s == "2":
            return "yaml"
        print("请输入 1 或 2")


def prompt_links_file() -> str:
    """Ask for a share-link file; if it does not exist, let the user paste links and create it."""
    path = input("分享链接文件路径 (默认 nodes.txt): ").strip() or "nodes.txt"
    p = Path(path).expanduser()
    if p.exists():
        return path
    print(f"{p} 不存在。请粘贴分享链接（vless:// vmess:// trojan:// ss:// hysteria2:// tuic:// anytls://），一行一个，空行结束：")
    lines: List[str] = []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    if not lines:
        raise ValueError("没有输入任何分享链接")
    _, problems = parse_links_text("\n".join(lines))
    for no, msg in problems:
        print(f"[警告] 第 {no} 行无法识别，将被忽略 - {msg}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[信息] 已写入 {p}（含密码/UUID，已在 .gitignore 中，请勿提交）")
    return path


def prompt_sources_interactive() -> Tuple[List[Source], List[Entry]]:
    n = prompt_int("要添加多少个订阅/节点来源？: ", 1, 50)
    sources: List[Source] = []
    entries: List[Entry] = []
    for i in range(n):
        print(f"\n--- 第 {i + 1} 个 ---")
        ns = sanitize_name(prompt_nonempty("名字(用于命名空间/Provider名): "), "订阅名字")
        url = prompt_nonempty("订阅链接(填 local 表示本地节点): ")
        snippet: Optional[str] = None
        links: Optional[str] = None
        if url.strip().lower() == "local":
            if prompt_local_kind() == "links":
                links = prompt_links_file()
            else:
                snippet = prompt_nonempty("本地节点 YAML 路径(含 proxies): ")
        else:
            snippet = input(f"片段路径(留空=需要时自动下载到 snippets/{ns}.yaml): ").strip() or None
        port = prompt_int("该来源的独立端口: ")
        # 只有分享链接的来源没有分组/规则，只能用模板
        profile = "template" if (links and not snippet) else prompt_profile()
        sources.append(Source(ns=ns, url=url.strip(), snippet=snippet, links=links))
        entries.append(Entry(name=ns, port=port, sources=[ns], profile=profile))

    if prompt_yes_no("\n是否添加汇总入口 ALL（包含全部来源，同时作为默认入口）？", True):
        port = prompt_int("ALL 端口: ")
        entries.append(Entry(name="ALL", port=port, sources=[s.ns for s in sources],
                             profile="template", default=True))
    else:
        entries[0].default = True
    return sources, entries


# -----------------------------
# sources.yaml
# -----------------------------
def check_structure(sources: List[Source], entries: List[Entry]) -> None:
    ns_set = [s.ns for s in sources]
    if len(set(ns_set)) != len(ns_set):
        raise ValueError("sources 里有重复的 ns")
    names = [e.name for e in entries]
    if len(set(names)) != len(names):
        raise ValueError("entries 里有重复的 name")
    if not entries:
        raise ValueError("entries 不能为空")
    for e in entries:
        if e.profile not in PROFILES:
            raise ValueError(f"入口 {e.name} 的 profile 必须是 {PROFILES}")
        if not e.sources:
            raise ValueError(f"入口 {e.name} 没有引用任何 source")
        for ns in e.sources:
            if ns not in ns_set:
                raise ValueError(f"入口 {e.name} 引用了不存在的 source: {ns}")
        if e.profile == "preserve" and len(e.sources) != 1:
            raise ValueError(f"入口 {e.name}: preserve 模式只能引用一个 source")
    for s in sources:
        if s.is_local and not (s.snippet or s.links):
            raise ValueError(f"source {s.ns} 是 local，必须提供 snippet 或 links")
    defaults = [e for e in entries if e.default]
    if len(defaults) > 1:
        raise ValueError("只能有一个 default 入口")
    if not defaults:
        entries[0].default = True


def load_sources(path: Path) -> Tuple[List[Source], List[Entry]]:
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是 key:value")
    sources = [
        Source(ns=sanitize_name(s["ns"], "ns"), url=str(s["url"]).strip(), snippet=s.get("snippet"),
               prefix=str(s.get("prefix") or ""), links=s.get("links"))
        for s in data.get("sources") or []
    ]
    entries = [
        Entry(
            name=sanitize_name(e["name"], "入口名"),
            port=int(e["port"]),
            sources=[str(x) for x in e.get("sources") or []],
            profile=str(e.get("profile", "template")),
            default=bool(e.get("default", False)),
        )
        for e in data.get("entries") or []
    ]
    check_structure(sources, entries)
    return sources, entries


def load_saved_ua(path: Path) -> Optional[str]:
    data = load_yaml_file(path)
    ua = data.get("ua") if isinstance(data, dict) else None
    return str(ua).strip() or None if ua else None


def needs_download(sources: List[Source], entries: List[Entry]) -> bool:
    """True if some preserve entry uses a remote source (the only case where the script downloads)."""
    remote = {s.ns for s in sources if not s.is_local}
    return any(e.profile == "preserve" and e.sources[0] in remote for e in entries)


def save_sources(path: Path, sources: List[Source], entries: List[Entry], ua: Optional[str] = None) -> None:
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    data = {
        **({"ua": ua} if ua else {}),
        "sources": [
            {k: v for k, v in (("ns", s.ns), ("url", s.url), ("snippet", s.snippet),
                               ("prefix", s.prefix or None), ("links", s.links)) if v is not None}
            for s in sources
        ],
        "entries": [
            {"name": e.name, "port": e.port, "sources": list(e.sources), "profile": e.profile,
             **({"default": True} if e.default else {})}
            for e in entries
        ],
    }
    dump_yaml_file(data, path)


def print_summary(sources: List[Source], entries: List[Entry]) -> None:
    print("\n当前配置：")
    for s in sources:
        kind = "local" if s.is_local else "远程订阅"
        print(f"  来源 {s.ns:<12} {kind}")
    for e in entries:
        flag = " [默认]" if e.default else ""
        print(f"  入口 {e.name:<12} 端口 {e.port:<6} {e.profile:<9} <- {', '.join(e.sources)}{flag}")
    print()


# -----------------------------
# Templates (config/*.yaml)
# -----------------------------
def load_templates(config_dir: Path) -> Templates:
    rs = load_yaml_file(config_dir / "rulesets.yaml") or {}
    defaults = rs.get("defaults") or {}
    rulesets: Dict[str, dict] = {}
    for name, v in (rs.get("rulesets") or {}).items():
        spec = dict(v) if isinstance(v, dict) else {"url": v}
        p = {**defaults, **spec}
        p.setdefault("path", f"./rules/{name}.{'yaml' if p.get('format') == 'yaml' else 'list'}")  # 扩展名跟随 format
        rulesets[name] = p

    groups = (load_yaml_file(config_dir / "groups.yaml") or {}).get("groups") or []
    rules = (load_yaml_file(config_dir / "rules.yaml") or {}).get("rules") or []
    if not groups or not rules:
        raise ValueError("groups.yaml / rules.yaml 不能为空")
    return Templates(groups=groups, rules=[str(r) for r in rules], rulesets=rulesets)


# -----------------------------
# Snippet fetching
# -----------------------------
def probe_ua(timeout: int = 180) -> Optional[str]:
    """
    Start a throw-away local HTTP server and wait for a Clash client to fetch it;
    return the User-Agent that client sent (None on timeout / Ctrl+C).
    Binds 127.0.0.1 on a free port; only the User-Agent header is read.
    """
    got: Dict[str, str] = {}
    done = threading.Event()
    body = b"proxies: []\nproxy-groups: []\nrules:\n  - MATCH,DIRECT\n"

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, head_only: bool) -> None:
            got.setdefault("ua", self.headers.get("User-Agent", "").strip())
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Subscription-Userinfo", "upload=0; download=0; total=0; expire=0")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            done.set()

        def do_GET(self) -> None:
            self._reply(False)

        def do_HEAD(self) -> None:
            self._reply(True)

        def log_message(self, *args: Any) -> None:  # silence default logging
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\n[探测] 已监听 http://127.0.0.1:{port}/", flush=True)
    print("       请在你的 Clash 客户端里新建订阅，地址填上面这个，然后点更新/下载。", flush=True)
    print(f"       最多等待 {timeout} 秒，Ctrl+C 跳过。探测完可在客户端里删除这个测试订阅。")
    try:
        waited = 0
        while not done.wait(1) and waited < timeout:
            waited += 1
    except KeyboardInterrupt:
        print("\n[探测] 已跳过")
    finally:
        server.shutdown()
        server.server_close()

    ua = got.get("ua") or None
    if ua:
        print(f"[探测] 收到 User-Agent: {ua}")
    else:
        print("[探测] 没有收到请求，将使用默认 UA")
    return ua


def read_links_file(path: str, ns: str) -> List[dict]:
    """Convert a file of share links (one per line) into mihomo proxies. Bad lines are skipped with a warning."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"找不到分享链接文件: {p}")
    proxies, problems = parse_links_text(p.read_text(encoding="utf-8"))
    for no, msg in problems:  # message never contains the link itself
        print(f"[警告] {p.name} 第 {no} 行已跳过 - {msg}")
    print(f"[信息] 来源 {ns}: 从 {p.name} 解析出 {len(proxies)} 个节点")
    return proxies


def redact_urls(text: str) -> str:
    """Keep only scheme://host of any URL, so tokens in query/path never reach logs."""
    text = re.sub(r"(https?://[^/\s'\"]+)[^\s'\"]*", r"\1/<已隐藏>", text)
    # urllib3 reports "with url: /path?query" (no host) on connection errors
    return re.sub(r"(url:\s*)/[^\s)'\"]*", r"\1/<已隐藏>", text)


class FetchError(ValueError):
    """Downloading a subscription snippet failed (network / blocked UA / not Clash YAML)."""


def fetch_snippet(src: Source, path: Path, refresh: bool, ua: str = SNIPPET_UA) -> None:
    """Download a Clash YAML subscription into `path` (cache). Base64 lists are not supported."""
    if path.exists() and not refresh:
        return
    try:
        import requests

        r = requests.get(src.url, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
        data = yaml.load(r.text)
        if not isinstance(data, dict) or not (data.get("proxies") or data.get("proxy-groups")):
            raise ValueError("返回内容不是 Clash YAML（可能是 Base64/v2ray 链接列表，暂不支持）")
    except Exception as e:  # noqa: BLE001
        msg = redact_urls(f"{type(e).__name__}: {e}")
        if path.exists():
            print(f"[警告] 下载 {src.ns} 失败，使用缓存 {path}: {msg}")
            return
        raise FetchError(f"下载订阅 {src.ns} 失败: {msg}") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    print(f"[信息] 已下载 {src.ns} -> {path}")


# -----------------------------
# preserve profile (keep the subscription's own groups/rules)
# -----------------------------
def build_maps(snippet: dict, group_prefix: str, name_map: Dict[str, str]) -> Maps:
    group_map = {
        g["name"]: f"{group_prefix}/{g['name']}"
        for g in snippet.get("proxy-groups") or []
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]
    }
    proxy_map = dict(name_map)
    rps = snippet.get("rule-providers") or {}
    ruleset_map = {k: f"{group_prefix}__{k}" for k in rps} if isinstance(rps, dict) else {}
    return Maps(group_map=group_map, proxy_map=proxy_map, ruleset_map=ruleset_map)


def raw_group_names(snippet: dict) -> Set[str]:
    return {
        g["name"]
        for g in snippet.get("proxy-groups") or []
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]
    }


def is_leaf_group(group: dict, raw_names: Set[str]) -> bool:
    proxies = group.get("proxies")
    if not isinstance(proxies, list):
        return True
    return not any(isinstance(x, str) and x in raw_names for x in proxies)


def namespace_rule_providers(rps: dict, maps: Maps) -> dict:
    out: dict = {}
    for k, v in (rps or {}).items():
        new_key = maps.ruleset_map.get(k, k)
        v2 = dict(v) if isinstance(v, dict) else v
        if isinstance(v2, dict) and v2.get("path"):
            ext = Path(str(v2["path"])).suffix or ".yaml"  # keep the original extension
            v2["path"] = f"./rules/{new_key}{ext}"  # avoid file clashes between entries
        out[new_key] = v2
    return out


def rewrite_rule_line(line: str, maps: Maps) -> str:
    parts = split_top_level_commas(line)
    if len(parts) < 2:
        return line
    if parts[0].strip() == "RULE-SET" and len(parts) >= 3:
        name = parts[1].strip()
        if name in maps.ruleset_map:
            parts[1] = maps.ruleset_map[name]
    idx = policy_index(parts)
    policy = parts[idx].strip()
    if policy in maps.group_map:
        parts[idx] = maps.group_map[policy]
    elif policy in maps.proxy_map:
        parts[idx] = maps.proxy_map[policy]
    return ",".join(parts)


def rewrite_group(
    g: dict, ns: str, maps: Maps, has_provider: bool, raw_names: Set[str], prefix: str = ""
) -> dict:
    """
    - rename group -> <prefix>/group
    - local: rewrite group / local-proxy references, keep everything else
    - provider leaf group: nodes -> use:[ns] + exact-match filter
    - provider non-leaf group: keep builtins + group refs, drop node names (add use if any dropped)
    """
    g2 = copy.deepcopy(g)
    old_name = g2.get("name")
    if isinstance(old_name, str) and old_name in maps.group_map:
        g2["name"] = maps.group_map[old_name]

    proxies_list = g2.get("proxies") if isinstance(g2.get("proxies"), list) else []

    if isinstance(g2.get("use"), list):
        g2["use"] = [ns if isinstance(u, str) and u in PROVIDER_PLACEHOLDERS else u for u in g2["use"]]

    if not has_provider:
        out: List[Any] = []
        for item in proxies_list:
            if not isinstance(item, str) or item in BUILTINS:
                out.append(item)
            else:
                out.append(maps.group_map.get(item) or maps.proxy_map.get(item) or item)
        if out:
            g2["proxies"] = out
        return g2

    if is_leaf_group(g, raw_names):
        if not proxies_list:
            g2["use"] = [ns]
            return g2
        nodes: List[str] = []
        kept: List[str] = []
        for item in proxies_list:
            if not isinstance(item, str):
                continue
            if item in BUILTINS:
                kept.append(item)
            elif item in raw_names:
                kept.append(maps.group_map.get(item, item))
            else:
                nodes.append(item if prefix and item.startswith(prefix) else f"{prefix}{item}")
        g2["use"] = [ns]
        g2["filter"] = regex_union_exact(nodes)
        if kept:
            g2["proxies"] = kept
        else:
            g2.pop("proxies", None)
        return g2

    kept2: List[str] = []
    dropped = False
    for item in proxies_list:
        if not isinstance(item, str):
            continue
        if item in BUILTINS:
            kept2.append(item)
        elif item in raw_names:
            kept2.append(maps.group_map.get(item, item))
        else:
            dropped = True
    if dropped:
        use_list = g2.get("use") if isinstance(g2.get("use"), list) else []
        if ns not in use_list:
            use_list.append(ns)
        g2["use"] = use_list
        print(f"[提示] 分组 {old_name}: 非叶子组中的节点名已丢弃，改用 use:[{ns}]")
    else:
        g2.pop("use", None)
    if kept2:
        g2["proxies"] = kept2
    elif not dropped:
        g2["proxies"] = ["DIRECT"]
    else:
        g2.pop("proxies", None)
    g2.pop("filter", None)
    g2.pop("exclude-filter", None)
    return g2


def build_preserve_entry(cfg: dict, entry: Entry, infos: Dict[str, SourceInfo]) -> str:
    ns = entry.sources[0]
    info = infos[ns]
    snippet = info.snippet or {}
    groups = snippet.get("proxy-groups") or []
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"来源 {ns} 的 snippet 缺少 proxy-groups（preserve 模式必需）")
    rules = snippet.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError(f"来源 {ns} 的 rules 必须是列表")

    maps = build_maps(snippet, entry.name, info.name_map)
    raw_names = raw_group_names(snippet)
    for g in groups:
        if isinstance(g, dict):
            cfg["proxy-groups"].append(
                rewrite_group(g, ns, maps, info.has_provider, raw_names, prefix=info.prefix))

    rps = snippet.get("rule-providers")
    if isinstance(rps, dict):
        for k, v in namespace_rule_providers(rps, maps).items():
            cfg["rule-providers"].setdefault(k, v)

    lines = [rewrite_rule_line(str(r), maps) for r in rules]
    if not any(r.strip().startswith("MATCH,") for r in lines):
        lines.append("MATCH,DIRECT")
    key = f"rules_{entry.name}"
    cfg["sub-rules"][key] = lines
    return key


# -----------------------------
# template profile
# -----------------------------
def build_template_entry(
    cfg: dict, entry: Entry, infos: Dict[str, SourceInfo], tpl: Templates, standalone: Set[str]
) -> str:
    prefix = entry.name
    shared, per_entry = template_group_names(tpl)
    remote = [ns for ns in entry.sources if infos[ns].has_provider]
    local = [n for ns in entry.sources for n in infos[ns].local_names]

    def resolve(item: str) -> str:
        return f"{prefix}/{item}" if item in per_entry else item  # shared groups keep their plain name

    for g in tpl.groups:
        scope = g.get("scope")
        if scope == SHARED or (scope == DEFAULT_ONLY and not entry.default):
            continue
        g2 = make_group(g, f"{prefix}/{g['name']}", resolve, remote, local)
        if g.get("standalone"):
            standalone.add(g2["name"])
        cfg["proxy-groups"].append(g2)

    group_map = {n: f"{prefix}/{n}" for n in per_entry}
    maps = Maps(group_map=group_map, proxy_map={}, ruleset_map={})
    key = f"rules_{entry.name}"
    # rules pointing at a default-only group (e.g. AI) are dropped for the other entries: the group does not exist there
    dropped = set() if entry.default else default_only_names(tpl)
    lines = [r for r in tpl.rules if rule_policy(r) not in dropped]
    cfg["sub-rules"][key] = [rewrite_rule_line(r, maps) for r in lines]
    return key


def default_only_names(tpl: Templates) -> Set[str]:
    return {g["name"] for g in tpl.groups if isinstance(g.get("name"), str) and g.get("scope") == DEFAULT_ONLY}


def rule_policy(line: str) -> str:
    parts = split_top_level_commas(line)
    return parts[policy_index(parts)] if len(parts) >= 2 else ""


def template_group_names(tpl: Templates) -> Tuple[Set[str], Set[str]]:
    """(shared group names, per-entry group names)."""
    shared = {g["name"] for g in tpl.groups if isinstance(g.get("name"), str) and g.get("scope") == SHARED}
    per_entry = {g["name"] for g in tpl.groups if isinstance(g.get("name"), str)} - shared
    return shared, per_entry


def make_group(g: dict, name: str, resolve: Any, remote: List[str], local: List[str]) -> dict:
    """Instantiate one template group: resolve group refs, expand LEAF into use (remote) + proxies (local)."""
    g2 = {k: copy.deepcopy(v) for k, v in g.items() if k not in {"proxies", "standalone", "scope"}}
    g2["name"] = name
    proxies: List[str] = []
    needs_leaf = False
    for item in g.get("proxies") or []:
        if item == LEAF:
            needs_leaf = True
        else:
            r = resolve(item) if isinstance(item, str) else item
            if isinstance(r, list):  # a shared group referencing a per-entry group fans out to every entry
                proxies.extend(r)
            else:
                proxies.append(r)
    if needs_leaf:
        proxies.extend(local)
        if remote:
            g2["use"] = list(remote)
    proxies = dedup(proxies)
    if proxies:
        g2["proxies"] = proxies
    elif not g2.get("use"):
        g2["proxies"] = ["DIRECT"]
    return g2


def build_shared_groups(
    cfg: dict, tpl: Templates, infos: Dict[str, SourceInfo], used_ns: List[str],
    entries: List[Entry], standalone: Set[str],
) -> None:
    """
    Groups with `scope: shared` are created once, without an entry prefix, over ALL sources.
    References: builtins and other shared groups stay as they are; a per-entry group name
    (e.g. "🚀 节点选择") expands to that group of EVERY template entry (main/🚀 节点选择, mix/...).
    """
    shared, per_entry = template_group_names(tpl)
    if not shared:
        return
    remote = [ns for ns in used_ns if infos[ns].has_provider]
    local = dedup([n for ns in used_ns for n in infos[ns].local_names])
    tpl_entries = [e for e in entries if e.profile == "template"]
    default_only = default_only_names(tpl)

    def resolve(item: str) -> Any:
        if item not in per_entry:
            return item
        # default-only groups exist only on the default entry; others exist on every template entry
        return [f"{e.name}/{item}" for e in tpl_entries if e.default or item not in default_only]

    for g in tpl.groups:
        if g.get("scope") != SHARED:
            continue
        g2 = make_group(g, g["name"], resolve, remote, local)
        if g.get("standalone"):
            standalone.add(g2["name"])
        cfg["proxy-groups"].append(g2)


def template_ruleset_refs(tpl: Templates) -> List[str]:
    refs: List[str] = []
    for r in tpl.rules:
        parts = split_top_level_commas(r)
        if parts[0] == "RULE-SET" and len(parts) >= 3:
            refs.append(parts[1])
    return dedup(refs)


# -----------------------------
# Build
# -----------------------------
def build_config(
    base: dict,
    tpl: Templates,
    sources: List[Source],
    entries: List[Entry],
    listen_addr: str = "127.0.0.1",
    refresh: bool = False,
    ua: str = SNIPPET_UA,
    standalone: Optional[Set[str]] = None,
) -> dict:
    """`standalone` (optional, filled in place): names of groups flagged standalone in groups.yaml."""
    standalone = standalone if standalone is not None else set()
    src_by_ns = {s.ns: s for s in sources}
    default_entry = next(e for e in entries if e.default)

    cfg: dict = substitute(copy.deepcopy(base or {}), DEFAULT_PLACEHOLDER, default_entry.name)
    cfg.setdefault("mode", "rule")
    for k, v in (("proxy-providers", {}), ("rule-providers", {}), ("proxies", []),
                 ("proxy-groups", []), ("listeners", []), ("sub-rules", {})):
        cfg[k] = cfg.get(k) or v

    used_ns = dedup([ns for e in entries for ns in e.sources])
    preserve_ns = {ns for e in entries if e.profile == "preserve" for ns in e.sources}

    # 1) sources: providers + snippets. Remote node names are only known when a snippet is loaded.
    infos: Dict[str, SourceInfo] = {}
    for ns in used_ns:
        src = src_by_ns[ns]
        info = SourceInfo(has_provider=not src.is_local, prefix=src.prefix)
        if info.has_provider:
            provider: dict = {
                "type": "http",
                "url": src.url.strip(),
                "path": f"./proxy_providers/{ns}.yaml",
                "interval": 3600,
                "health-check": {
                    "enable": True,
                    "url": "https://www.gstatic.com/generate_204",
                    "interval": 300,
                    "lazy": True,
                    "expected-status": 204,
                },
            }
            if src.prefix:  # no prefix by default: keep the subscription's original node names
                provider["override"] = {"additional-prefix": src.prefix}
            cfg["proxy-providers"][ns] = provider
        if src.is_local:
            if src.snippet:
                snippet = load_yaml_file(src.snippet)
                if not isinstance(snippet, dict):
                    raise ValueError(f"{src.snippet} 不是 YAML dict")
                info.snippet = snippet
                info.proxies += [p for p in snippet.get("proxies") or [] if isinstance(p, dict)]
            if src.links:
                info.proxies += read_links_file(src.links, ns)
        elif ns in preserve_ns:
            path = Path(src.snippet or f"snippets/{ns}.yaml")
            fetch_snippet(src, path, refresh, ua)
            snippet = load_yaml_file(path)
            if not isinstance(snippet, dict):
                raise ValueError(f"{path} 不是 YAML dict")
            info.snippet = snippet
            for p in snippet.get("proxies") or []:
                if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]:
                    info.name_map[p["name"]] = f"{src.prefix}{p['name']}"
        infos[ns] = info

    # 1b) local nodes keep their names; a local node is renamed only when it clashes with an
    #     already-known node (known remote names, or an earlier local node). Remote-vs-remote
    #     clashes are not handled (and, for template mode, cannot be seen before mihomo fetches).
    taken: Set[str] = {n for i in infos.values() if i.has_provider for n in i.name_map.values()}
    seen: Dict[Tuple[str, str], str] = {}  # (wanted name, content) -> final name; identical local nodes merge
    for ns in used_ns:
        src = src_by_ns[ns]
        if not src.is_local:
            continue
        info = infos[ns]
        for p in info.proxies:
            if not (isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]):
                continue
            orig = p["name"]
            name = f"{src.prefix}{orig}"
            content = json.dumps({k: v for k, v in p.items() if k != "name"}, sort_keys=True, default=str)
            if (name, content) in seen:  # same name and same content as an earlier local node: keep one copy
                final = seen[(name, content)]
                if final not in info.local_names:
                    info.local_names.append(final)
                info.name_map[orig] = final
                continue
            if name in taken:
                new = unique_name(name, ns, taken)
                print(f"[提示] 本地节点 {name} 与已有节点重名，已重命名为 {new}")
                name = new
            taken.add(name)
            seen[(f"{src.prefix}{orig}", content)] = name
            p2 = dict(p)
            p2["name"] = name
            cfg["proxies"].append(p2)
            info.local_names.append(name)
            info.name_map[orig] = name
        if not info.local_names:
            print(f"[警告] 本地来源 {ns} 没有可用的 proxies")

    # 2) entries
    for e in entries:
        if e.profile == "preserve":
            key = build_preserve_entry(cfg, e, infos)
        else:
            key = build_template_entry(cfg, e, infos, tpl, standalone)
        cfg["listeners"].append({
            "name": f"in-{e.name}",
            "type": "mixed",
            "listen": listen_addr,
            "port": int(e.port),
            "udp": True,
            "rule": key,
        })

    # 2b) shared groups (e.g. the single latency-test group over all nodes)
    if any(e.profile == "template" for e in entries):
        build_shared_groups(cfg, tpl, infos, used_ns, entries, standalone)

    # 3) rule-providers used by the template
    if any(e.profile == "template" for e in entries):
        refs = template_ruleset_refs(tpl)
        for name in refs:
            if name not in tpl.rulesets:
                raise ValueError(f"rules.yaml 引用了未在 rulesets.yaml 声明的规则集: {name}")
            cfg["rule-providers"].setdefault(name, copy.deepcopy(tpl.rulesets[name]))
        unused = [n for n in tpl.rulesets if n not in refs]
        if unused:
            print(f"[警告] rulesets.yaml 中声明但未被 rules.yaml 使用，已忽略: {', '.join(unused)}")

    # 4) global rules come from the default entry
    cfg["rules"] = list(cfg["sub-rules"][f"rules_{default_entry.name}"])
    return tidy_order(cfg)


TOP_LEVEL_ORDER = ("rule-providers", "proxy-providers", "proxies", "proxy-groups", "listeners", "sub-rules", "rules")


def tidy_order(cfg: dict) -> dict:
    """Readable key order: base settings, rule-providers, then proxy-providers, proxies and groups, then listeners and rules;
    inside a group `use` (providers) comes right before `proxies`."""
    managed = set(TOP_LEVEL_ORDER)
    out = {k: v for k, v in cfg.items() if k not in managed}
    for k in TOP_LEVEL_ORDER:
        if k in cfg:
            out[k] = cfg[k]
    groups = []
    for g in out.get("proxy-groups", []):
        if isinstance(g, dict) and "use" in g and "proxies" in g:
            g2: dict = {}
            for k, v in g.items():
                if k == "use":
                    continue
                if k == "proxies":
                    g2["use"] = g["use"]
                g2[k] = v
            g = g2
        groups.append(g)
    if "proxy-groups" in out:
        out["proxy-groups"] = groups
    return out


# -----------------------------
# Validation
# -----------------------------
def validate(cfg: dict, standalone: Optional[Set[str]] = None) -> Tuple[List[str], List[str]]:
    standalone = standalone or set()
    errors: List[str] = []
    warnings: List[str] = []

    group_names = [g["name"] for g in cfg["proxy-groups"] if isinstance(g, dict) and "name" in g]
    proxy_names = [p["name"] for p in cfg["proxies"] if isinstance(p, dict) and "name" in p]
    for label, names in (("分组", group_names), ("节点", proxy_names)):
        for n in {x for x in names if names.count(x) > 1}:
            errors.append(f"重复的{label}名: {n}")
    for n in set(group_names) & set(proxy_names):
        errors.append(f"分组与节点重名: {n}")

    providers = set(cfg["proxy-providers"])
    rps = set(cfg["rule-providers"])
    known = set(group_names) | set(proxy_names) | BUILTINS

    referenced: Set[str] = set()
    for g in cfg["proxy-groups"]:
        for item in g.get("proxies") or []:
            if isinstance(item, str):
                referenced.add(item)
                if item not in known:
                    warnings.append(f"分组 {g['name']} 引用了不存在的目标: {item}")
        for u in g.get("use") or []:
            if u not in providers:
                errors.append(f"分组 {g['name']} 的 use 引用了不存在的 provider: {u}")

    rule_lists = list(cfg["sub-rules"].values()) + [cfg["rules"]]
    used_rps: Set[str] = set()
    for rules in rule_lists:
        for line in rules:
            parts = split_top_level_commas(str(line))
            kind = parts[0]
            if kind == "RULE-SET" and len(parts) >= 3:
                used_rps.add(parts[1])
                if parts[1] not in rps:
                    errors.append(f"规则引用了不存在的规则集: {line}")
            if kind in {"SUB-RULE", "AND", "OR", "NOT"} or len(parts) < 2:
                continue
            policy = parts[policy_index(parts)]
            referenced.add(policy)
            if policy not in known:
                warnings.append(f"规则的策略不存在: {line}")

    for k in sorted(rps - used_rps):
        warnings.append(f"规则集 {k} 已定义但未被任何规则使用")
    for n in group_names:
        if n not in referenced and n not in standalone:
            warnings.append(f"分组 {n} 未被任何规则或分组引用")

    ports: Dict[int, str] = {}
    for ln in cfg["listeners"]:
        if ln["port"] in ports:
            errors.append(f"端口冲突: {ln['port']} ({ports[ln['port']]} / {ln['name']})")
        ports[ln["port"]] = ln["name"]
        if ln["rule"] not in cfg["sub-rules"]:
            errors.append(f"监听 {ln['name']} 引用了不存在的 sub-rule: {ln['rule']}")

    dns = cfg.get("dns") or {}
    for key in ("nameserver", "fallback", "default-nameserver", "proxy-server-nameserver"):
        for s in dns.get(key) or []:
            if isinstance(s, str) and "#" in s:
                target = s.split("#", 1)[1]
                if target not in known:
                    warnings.append(f"dns.{key} 指向了不存在的分组: {target}")
    return errors, warnings


# -----------------------------
# Entrypoint
# -----------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="多订阅多端口 -> mihomo config")
    ap.add_argument("--config-dir", default=str(HERE / "config"), help="模板目录（base/rulesets/groups/rules）")
    ap.add_argument("--base", help="可选：覆盖 config/base.yaml")
    ap.add_argument("--sources", default="sources.yaml", help="订阅/入口配置文件（不存在则交互生成）")
    ap.add_argument("--out", default="merged.yaml", help="输出文件路径")
    ap.add_argument("--listen", default="127.0.0.1", help="listeners 监听地址")
    ap.add_argument("--yes", action="store_true", help="存在 sources.yaml 时直接复用，不询问")
    ap.add_argument("--refresh", action="store_true", help="强制重新下载订阅 snippet")
    ap.add_argument("--ua", help=f"下载订阅时使用的 User-Agent（优先级：--ua > sources.yaml 的 ua > 默认 {SNIPPET_UA}）")
    ap.add_argument("--probe-ua", action="store_true", help="探测本机 Clash 客户端的 User-Agent 并保存到 sources.yaml")
    args = ap.parse_args()

    config_dir = Path(args.config_dir).expanduser().resolve()
    tpl = load_templates(config_dir)
    base = load_yaml_file(args.base or config_dir / "base.yaml") or {}

    sources_path = Path(args.sources).expanduser().resolve()
    reuse = False
    saved_ua: Optional[str] = None
    if sources_path.exists():
        sources, entries = load_sources(sources_path)
        saved_ua = load_saved_ua(sources_path)
        print_summary(sources, entries)
        reuse = args.yes or prompt_yes_no(f"发现 {sources_path.name}，是否复用？", True)
    if not reuse:
        sources, entries = prompt_sources_interactive()
        check_structure(sources, entries)
        saved_ua = None
        # 只有 preserve + 远程订阅才会由脚本下载订阅，此时才有必要问 UA
        if not args.probe_ua and not args.ua and needs_download(sources, entries):
            args.probe_ua = prompt_yes_no(
                f"\npreserve 模式需要下载订阅，默认 UA 为 {SNIPPET_UA}。是否用本机 Clash 客户端探测真实 UA？", False)

    ua = args.ua or saved_ua
    probed = False
    if args.probe_ua:
        found = probe_ua()
        if found:
            ua, probed = found, True

    if not reuse or probed:
        save_sources(sources_path, sources, entries, ua=ua if (probed or saved_ua) else None)
        print(f"[信息] 已保存配置：{sources_path}")

    standalone: Set[str] = set()
    try:
        cfg = build_config(base, tpl, sources, entries, listen_addr=args.listen,
                           refresh=args.refresh, standalone=standalone, ua=ua or SNIPPET_UA)
    except FetchError as e:
        # 下载订阅失败，常见原因是机场按 UA 拦截；交互模式下提示探测 UA 并重试一次
        print(f"[错误] {e}")
        if args.yes or args.probe_ua or not prompt_yes_no("是否探测本机 Clash 客户端的 UA 后重试？", True):
            raise SystemExit(2)
        found = probe_ua()
        if not found:
            raise SystemExit(2)
        ua = found
        save_sources(sources_path, sources, entries, ua=ua)
        print(f"[信息] 已将 UA 保存到 {sources_path}")
        standalone.clear()
        cfg = build_config(base, tpl, sources, entries, listen_addr=args.listen,
                           refresh=True, standalone=standalone, ua=ua)

    errors, warnings = validate(cfg, standalone)
    for w in warnings:
        print(f"[警告] {w}")
    if errors:
        for e in errors:
            print(f"[错误] {e}")
        raise SystemExit(2)

    out_path = Path(args.out).expanduser().resolve()
    dump_yaml_file(cfg, out_path)
    print(f"\n[完成] 已生成：{out_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"\n[错误] {e}")
        sys.exit(2)
