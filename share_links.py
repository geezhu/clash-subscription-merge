# -*- coding: utf-8 -*-
"""
分享链接 (vless:// vmess:// trojan:// ss:// hysteria2:// tuic:// anytls://) -> mihomo 节点 dict。

字段映射参考 mack-a/v2ray-agent 生成的链接与它输出的 Clash.Meta 片段。
解析失败只会跳过该行并给出原因；提示信息里**不会**包含链接内容（里面有密码/UUID）。
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit


class LinkError(ValueError):
    """The link cannot be converted (bad format / unsupported feature)."""


# -----------------------------
# helpers
# -----------------------------
def _b64decode(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8")


def _query(q: str) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(q, keep_blank_values=True).items()}


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _list(v: Optional[str]) -> Optional[List[str]]:
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    return items or None


def _split(link: str) -> Tuple[Any, str, int, Dict[str, str], str]:
    u = urlsplit(link)
    try:
        port = u.port
    except ValueError:
        raise LinkError("端口无效") from None
    if not u.hostname or port is None:
        raise LinkError("缺少主机或端口")
    return u, u.hostname, port, _query(u.query), unquote(u.fragment)


def _clean(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in p.items() if v is not None and v != ""}


def _transport(p: Dict[str, Any], net: Optional[str], path: Optional[str], host: Optional[str],
               service: Optional[str], mode: Optional[str] = None) -> None:
    net = (net or "tcp").lower()
    if net == "tcp":
        p["network"] = "tcp"
    elif net == "ws":
        p["network"] = "ws"
        opts: Dict[str, Any] = {"path": path or "/"}
        if host:
            opts["headers"] = {"Host": host}
        p["ws-opts"] = opts
    elif net == "grpc":
        p["network"] = "grpc"
        p["grpc-opts"] = {"grpc-service-name": service or path or ""}
    elif net == "xhttp":
        p["network"] = "xhttp"
        opts = {"path": path or "/"}
        if host:
            opts["host"] = host
        if mode:
            opts["mode"] = mode
        p["xhttp-opts"] = opts
    elif net in {"h2", "http"}:
        p["network"] = "h2"
        p["h2-opts"] = _clean({"path": path or "/", "host": [host] if host else None})
    else:
        raise LinkError(f"不支持的传输方式: {net}")


def _tls_common(p: Dict[str, Any], q: Dict[str, str], sni_keys: Tuple[str, ...] = ("sni", "peer")) -> None:
    for k in sni_keys:
        if q.get(k):
            p["servername" if p["type"] in {"vless", "vmess"} else "sni"] = q[k]
            break
    if q.get("fp"):
        p["client-fingerprint"] = q["fp"]
    if _list(q.get("alpn")):
        p["alpn"] = _list(q["alpn"])
    if _truthy(q.get("allowInsecure", q.get("insecure", q.get("allow_insecure", "0")))):
        p["skip-cert-verify"] = True


# -----------------------------
# protocols
# -----------------------------
def parse_vless(link: str) -> Dict[str, Any]:
    u, host, port, q, name = _split(link)
    uuid = unquote(u.username or "")
    if not uuid:
        raise LinkError("缺少 UUID")
    p: Dict[str, Any] = {"name": name or f"vless-{host}:{port}", "type": "vless",
                         "server": host, "port": port, "uuid": uuid, "udp": True}
    _transport(p, q.get("type"), q.get("path"), q.get("host") or q.get("sni"),
               q.get("serviceName"), q.get("mode"))
    security = (q.get("security") or "none").lower()
    if security in {"tls", "reality"}:
        p["tls"] = True
        _tls_common(p, q, ("sni",))
    if security == "reality":
        if not q.get("pbk"):
            raise LinkError("reality 缺少 pbk（公钥）")
        p["reality-opts"] = _clean({"public-key": q["pbk"], "short-id": q.get("sid")})
    elif security not in {"tls", "none"}:
        raise LinkError(f"不支持的 security: {security}")
    if q.get("flow"):
        p["flow"] = q["flow"]
    return _clean(p)


def parse_vmess(link: str) -> Dict[str, Any]:
    body = link[len("vmess://"):].split("#", 1)[0]
    try:
        d = json.loads(_b64decode(body))
        host, port, uuid = d["add"], int(d["port"]), d["id"]
    except Exception:  # noqa: BLE001
        raise LinkError("vmess 内容不是有效的 base64 JSON") from None
    p: Dict[str, Any] = {"name": d.get("ps") or f"vmess-{host}:{port}", "type": "vmess",
                         "server": host, "port": port, "uuid": uuid,
                         "alterId": int(d.get("aid") or 0), "cipher": d.get("scy") or "auto", "udp": True}
    _transport(p, d.get("net"), d.get("path"), d.get("host") or d.get("sni"), d.get("path"))
    if str(d.get("tls", "")).lower() == "tls" or d.get("tls") is True:
        p["tls"] = True
        q = {"sni": d.get("sni") or d.get("host") or "", "fp": d.get("fp") or "", "alpn": d.get("alpn") or "",
             "allowInsecure": str(d.get("allowInsecure", "0"))}
        _tls_common(p, q, ("sni",))
    return _clean(p)


def parse_trojan(link: str) -> Dict[str, Any]:
    u, host, port, q, name = _split(link)
    password = unquote(u.username or "")
    if not password:
        raise LinkError("缺少密码")
    p: Dict[str, Any] = {"name": name or f"trojan-{host}:{port}", "type": "trojan",
                         "server": host, "port": port, "password": password, "udp": True}
    _tls_common(p, q, ("sni", "peer"))
    net = (q.get("type") or "tcp").lower()
    if net != "tcp":
        _transport(p, net, q.get("path"), q.get("host"), q.get("serviceName"))
    return _clean(p)


def parse_ss(link: str) -> Dict[str, Any]:
    raw = link[len("ss://"):]
    raw, _, frag = raw.partition("#")
    name = unquote(frag)
    raw, _, query = raw.partition("?")
    if "plugin" in _query(query):
        raise LinkError("ss 插件暂不支持")
    raw = raw.rstrip("/")
    try:
        if "@" in raw:  # SIP002: base64url(method:password)@host:port
            userinfo, _, hostport = raw.rpartition("@")
            if ":" not in userinfo:
                userinfo = _b64decode(unquote(userinfo))
            else:
                userinfo = unquote(userinfo)
        else:  # legacy: base64(method:password@host:port)
            decoded = _b64decode(unquote(raw))
            userinfo, _, hostport = decoded.rpartition("@")
        method, _, password = userinfo.partition(":")
        hp = urlsplit("//" + hostport)
        host, port = hp.hostname, hp.port
    except Exception:  # noqa: BLE001
        raise LinkError("ss 链接格式无法解析") from None
    if not (method and host and port):
        raise LinkError("ss 链接缺少加密方式/主机/端口")
    return _clean({"name": name or f"ss-{host}:{port}", "type": "ss", "server": host, "port": port,
                   "cipher": method, "password": password, "udp": True})


def parse_hysteria2(link: str) -> Dict[str, Any]:
    u, host, port, q, name = _split(link)
    password = unquote(u.username or "")
    if u.password:
        password += ":" + unquote(u.password)
    if not password:
        raise LinkError("缺少密码")
    p: Dict[str, Any] = {"name": name or f"hysteria2-{host}:{port}", "type": "hysteria2",
                         "server": host, "port": port, "password": password}
    _tls_common(p, q, ("sni", "peer"))
    if q.get("mport"):
        p["ports"] = q["mport"]
    if q.get("upmbps"):
        p["up"] = f"{q['upmbps']} Mbps"
    if q.get("downmbps"):
        p["down"] = f"{q['downmbps']} Mbps"
    if q.get("obfs"):
        p["obfs"] = q["obfs"]
        p["obfs-password"] = q.get("obfs-password")
    return _clean(p)


def parse_tuic(link: str) -> Dict[str, Any]:
    u, host, port, q, name = _split(link)
    uuid, password = unquote(u.username or ""), unquote(u.password or "")
    if not uuid or not password:
        raise LinkError("tuic 需要 uuid:密码")
    p: Dict[str, Any] = {"name": name or f"tuic-{host}:{port}", "type": "tuic",
                         "server": host, "port": port, "uuid": uuid, "password": password,
                         "congestion-controller": q.get("congestion_control"),
                         "udp-relay-mode": q.get("udp_relay_mode")}
    _tls_common(p, q, ("sni",))
    return _clean(p)


def parse_anytls(link: str) -> Dict[str, Any]:
    u, host, port, q, name = _split(link)
    password = unquote(u.username or "")
    if not password:
        raise LinkError("缺少密码")
    p: Dict[str, Any] = {"name": name or f"anytls-{host}:{port}", "type": "anytls",
                         "server": host, "port": port, "password": password, "udp": True}
    _tls_common(p, q, ("sni", "peer"))
    return _clean(p)


PARSERS = {
    "vless": parse_vless,
    "vmess": parse_vmess,
    "trojan": parse_trojan,
    "ss": parse_ss,
    "hysteria2": parse_hysteria2,
    "hy2": parse_hysteria2,
    "tuic": parse_tuic,
    "anytls": parse_anytls,
}


def parse_link(line: str) -> Dict[str, Any]:
    scheme = line.split("://", 1)[0].lower() if "://" in line else ""
    if scheme.startswith("naive"):
        raise LinkError("mihomo 不支持 naive")
    parser = PARSERS.get(scheme)
    if parser is None:
        raise LinkError(f"不支持的协议: {scheme or '无法识别'}")
    return parser(line.strip())


def parse_links_text(text: str) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str]]]:
    """
    One link per line; blank lines and lines starting with '#' are ignored.
    Returns (proxies, problems) where problems = [(line number, message)] and never contains the link itself.
    """
    proxies: List[Dict[str, Any]] = []
    problems: List[Tuple[int, str]] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            proxies.append(parse_link(line))
        except LinkError as e:
            scheme = line.split("://", 1)[0] if "://" in line else "?"
            problems.append((no, f"{scheme}: {e}"))
        except Exception as e:  # noqa: BLE001
            problems.append((no, f"解析出错: {type(e).__name__}"))
    return proxies, problems
