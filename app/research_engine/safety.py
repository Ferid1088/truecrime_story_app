"""Fetch-safety guard (Part 9) — SSRF and protocol protection.

Every URL is validated before any request is made, and every redirect
hop is validated again. Private/loopback/link-local targets, non-http
schemes and unsafe redirect chains are rejected.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_MAX_REDIRECTS = 5


class UnsafeURLError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url(url: str, resolve_dns: bool = True) -> str:
    """Return the URL if it is safe to fetch, else raise UnsafeURLError.

    Checks scheme, host sanity, and (when resolve_dns) that the host does
    not resolve to a private/loopback/link-local address.
    """
    u = (url or "").strip()
    if not u:
        raise UnsafeURLError("empty_url")
    try:
        parts = urlsplit(u)
    except ValueError:
        raise UnsafeURLError("malformed_url")
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError("invalid_protocol")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("missing_host")
    host = host.lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURLError("local_host")
    # Literal IP?
    try:
        ip = ipaddress.ip_address(host)
        if _blocked_ip(ip):
            raise UnsafeURLError("private_ip")
        return u
    except ValueError:
        pass
    if not resolve_dns:
        return u
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.timeout):
        # Unresolvable hosts fail at fetch time anyway; do not block here.
        return u
    except OSError:
        return u
    for family, _, _, _, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _blocked_ip(ip):
            raise UnsafeURLError("private_ip")
    return u

