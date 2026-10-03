"""Server-side request forgery guard for every URL a connector fetches or posts to.

``check_url`` validates the URL and *every* address its name resolves to (one private answer among
public ones is a rebinding attempt, so the URL is refused). ``SafeHttp`` re-runs the check on each
redirect hop instead of letting httpx follow redirects.

Known limit (TOCTOU): DNS can change between our lookup and the socket connect, so the guard narrows
the window but cannot close it. Closing it needs connecting to the validated IP with the Host/SNI
preserved (or an egress firewall); that is NOT implemented here. Treat this as defence in depth next
to network-level egress rules, not as the only control.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from patchquest.domain.failures import FailureKind, PatchQuestError

Resolver = Callable[..., Iterable[Any]]
_V4 = ipaddress.IPv4Address
_V6 = ipaddress.IPv6Address
# is_global says True for these; each can embed or alias an internal IPv4 address.
_V6_BLOCKED = tuple(ipaddress.ip_network(n) for n in ("::/96", "64:ff9b::/96", "2002::/16", "2001::/32", "100::/64"))
_MAX_URL = 2048
_STRIP_ON_HOST_CHANGE = ("authorization", "cookie", "proxy-authorization")
MAX_RESPONSE_BYTES = 1 << 20


class SSRFBlocked(PatchQuestError):
    """The URL points somewhere connectors must not reach. Permanent: retrying cannot help."""

    def __init__(self, reason: str) -> None:
        super().__init__(FailureKind.TOOL_FAILURE, f"blocked URL: {reason}")


def host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    """Exact host, or ``*.suffix`` for subdomains of suffix (not the bare suffix itself)."""
    host = host.lower()
    for entry in allowlist:
        entry = entry.lower()
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif host == entry:
            return True
    return False


def _is_public(ip: _V4 | _V6) -> bool:
    if isinstance(ip, _V6):
        if ip.ipv4_mapped is not None:
            return _is_public(ip.ipv4_mapped)
        if any(ip in net for net in _V6_BLOCKED):
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback
                                 or ip.is_link_local)


def _literal_ip(host: str) -> _V4 | _V6 | None:
    """The IP a host spelling denotes, or None for a DNS name. Odd numeric spellings are decoded, not trusted."""
    if ":" in host:
        if "%" in host:
            raise SSRFBlocked("IPv6 zone identifiers are not allowed")
        try:
            return _V6(host)
        except ValueError:
            raise SSRFBlocked("malformed IPv6 host") from None
    if host.rsplit(".", 1)[-1][:1].isdigit():  # "ends in a number": WHATWG says this is an IPv4 spelling
        try:
            return _V4(socket.inet_aton(host))  # decimal, hex, octal and short forms (127.1, 0x7f000001, 0177.0.0.1)
        except OSError:
            raise SSRFBlocked("malformed numeric host") from None
    return None


def check_url(url: str, *, resolve: Resolver = socket.getaddrinfo, allowlist: Iterable[str] | None = None) -> tuple[str, ...]:
    """Raise ``SSRFBlocked`` unless ``url`` is a plain http(s) URL whose every address is public.

    Returns the validated addresses. An allowlist narrows further (host must match) but never waives the
    address checks.
    """
    if not url or len(url) > _MAX_URL or any(c.isspace() or c in "\\\x00" or ord(c) < 32 for c in url):
        raise SSRFBlocked("empty, oversized or contains control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise SSRFBlocked("unparseable URL") from None
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"scheme {parts.scheme!r} is not allowed")
    if "@" in parts.netloc:
        raise SSRFBlocked("credentials / userinfo in the URL")
    if not host:
        raise SSRFBlocked("no host")
    host = host.rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        raise SSRFBlocked("host is local")
    if allowlist is not None and not host_allowed(host, allowlist):
        raise SSRFBlocked("host is not on the workspace allowlist")
    literal = _literal_ip(host)
    if literal is not None:
        addresses: list[str] = [str(literal)]
    else:
        try:
            infos = list(resolve(host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM))
        except socket.gaierror:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "DNS resolution failed") from None
        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "DNS returned no addresses")
    for text in addresses:
        if "%" in text:
            raise SSRFBlocked("resolved to a scoped address")
        if not _is_public(ipaddress.ip_address(text)):
            raise SSRFBlocked(f"{host} resolves to a non-public address")
    return tuple(addresses)


def ensure_ok(response: httpx.Response) -> httpx.Response:
    """Map a failed response onto the central failure table.

    The table treats most 4xx on connectors as retryable; a deterministic client error (bad request,
    not found, validation) cannot be fixed by repeating it, so it is surfaced as a permanent failure.
    429/408/409/425/5xx stay with ``classify`` (and ``Retry-After``); 401/403 are CONNECTOR_AUTH.
    """
    status = response.status_code
    if 200 <= status < 300:
        return response
    if status in (401, 403, 408, 409, 425, 429) or status >= 500:
        response.raise_for_status()  # always raises here: classify() reads Retry-After from the response
    raise PatchQuestError(FailureKind.TOOL_FAILURE, f"HTTP {status}")


class SafeHttp:
    """httpx wrapper: SSRF-checked, no automatic redirects, bounded response size."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None, resolve: Resolver = socket.getaddrinfo,
                 allowlist: Iterable[str] | None = None, max_redirects: int = 3, timeout_s: float = 10.0) -> None:
        self._transport, self._resolve, self._timeout = transport, resolve, timeout_s
        self._allowlist = None if allowlist is None else tuple(allowlist)
        self._max_redirects = max_redirects

    def check(self, url: str) -> tuple[str, ...]:
        return check_url(url, resolve=self._resolve, allowlist=self._allowlist)

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None, content: bytes | None = None,
                params: Mapping[str, str] | None = None, check: bool = True) -> httpx.Response:
        hdrs = dict(headers or {})
        with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False) as client:
            for _hop in range(self._max_redirects + 1):
                self.check(url)
                response = self._send(client, method, url, hdrs, content, params)
                location = response.headers.get("location")
                if response.status_code not in (301, 302, 303, 307, 308) or not location:
                    return ensure_ok(response) if check else response
                target = urljoin(url, location)
                if urlsplit(target).netloc.lower() != urlsplit(url).netloc.lower():
                    hdrs = {k: v for k, v in hdrs.items() if k.lower() not in _STRIP_ON_HOST_CHANGE}
                if response.status_code in (301, 302, 303):
                    method, content = ("GET" if method != "HEAD" else "HEAD"), None
                url, params = target, None
        raise PatchQuestError(FailureKind.TOOL_FAILURE, f"more than {self._max_redirects} redirects")

    @staticmethod
    def _send(client: httpx.Client, method: str, url: str, headers: dict[str, str], content: bytes | None,
              params: Mapping[str, str] | None) -> httpx.Response:
        req = client.build_request(method, url, headers=headers, content=content, params=params)
        resp = client.send(req, stream=True)
        try:
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PatchQuestError(FailureKind.TOOL_FAILURE, "response body too large")
        finally:
            resp.close()
        kept = httpx.Headers(resp.headers)
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            kept.pop(name, None)
        return httpx.Response(resp.status_code, headers=kept, content=bytes(body), request=req)
