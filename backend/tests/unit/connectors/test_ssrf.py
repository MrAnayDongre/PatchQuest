from __future__ import annotations

import httpx
import pytest

from patchquest.connectors.ssrf import SafeHttp, SSRFBlocked, check_url, host_allowed
from patchquest.domain.failures import FailureKind, PatchQuestError
from tests.unit.connectors.conftest import resolver

BLOCKED_LITERALS = [
    "10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.1.1", "127.0.0.1", "127.255.255.254",
    "0.0.0.0", "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.255", "224.0.0.1", "255.255.255.255",
    "[::1]", "[::]", "[fc00::1]", "[fd12:3456::1]", "[fe80::1]", "[::ffff:127.0.0.1]", "[::ffff:10.0.0.1]",
    "[::ffff:169.254.169.254]", "[64:ff9b::7f00:1]", "[2002:7f00:1::]", "[::7f00:1]", "[ff02::1]",
    "2130706433", "0x7f000001", "0177.0.0.1", "127.1", "0x7f.1", "017700000001", "2852039166", "0xA9FEA9FE",
    "999999999999", "1.2.3.256", "localhost", "LOCALHOST.", "a.localhost",
]


@pytest.mark.parametrize("host", BLOCKED_LITERALS)
def test_blocked_hosts(host):
    with pytest.raises(PatchQuestError):
        check_url(f"http://{host}/x", resolve=resolver())


@pytest.mark.parametrize("url", [
    "http://good@evil.example/", "http://user:pw@example.com/", "file:///etc/passwd", "gopher://example.com/",
    "ftp://example.com/", "javascript:alert(1)", "//example.com/", "http:///path", "", "http://", "http://exa mple.com/",
    "http://example.com\\@127.0.0.1/", "http://[::1/", "http://example.com:99999/", "http://[fe80::1%25eth0]/",
    "http://example.com/\x00", "http://" + "a" * 3000,
])
def test_blocked_urls(url):
    with pytest.raises(SSRFBlocked):
        check_url(url, resolve=resolver())


@pytest.mark.parametrize("url", ["https://example.com/a", "http://93.184.216.34:8080/", "https://[2606:4700::1111]/", "https://example.com./"])
def test_public_urls_pass(url):
    assert check_url(url, resolve=resolver())


def test_dns_rebinding_one_private_answer_blocks():
    res = resolver({"rebind.example": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(SSRFBlocked, match="non-public"):
        check_url("https://rebind.example/", resolve=res)


def test_dns_to_ipv6_mapped_loopback_blocks():
    with pytest.raises(SSRFBlocked):
        check_url("https://x.example/", resolve=resolver({"x.example": ["::ffff:7f00:1"]}))


def test_dns_failure_is_transient_not_blocked():
    def boom(*_a, **_k):
        import socket
        raise socket.gaierror("nope")

    with pytest.raises(PatchQuestError) as err:
        check_url("https://x.example/", resolve=boom)
    assert err.value.kind is FailureKind.CONNECTOR_UNAVAILABLE


def test_allowlist_exact_and_suffix_and_never_waives_ip_checks():
    allow = ["api.example.com", "*.corp.example"]
    assert check_url("https://api.example.com/", resolve=resolver(), allowlist=allow)
    assert check_url("https://a.b.corp.example/", resolve=resolver(), allowlist=allow)
    for bad in ("https://corp.example/", "https://evilcorp.example/", "https://api.example.com.evil.io/"):
        with pytest.raises(SSRFBlocked, match="allowlist"):
            check_url(bad, resolve=resolver(), allowlist=allow)
    with pytest.raises(SSRFBlocked):
        check_url("http://127.0.0.1/", resolve=resolver(), allowlist=["127.0.0.1"])
    with pytest.raises(SSRFBlocked, match="non-public"):
        check_url("https://api.example.com/", resolve=resolver({"api.example.com": ["10.1.1.1"]}), allowlist=allow)
    assert host_allowed("A.CORP.EXAMPLE", ["*.corp.example"])


def _http(handler):
    return SafeHttp(transport=httpx.MockTransport(handler), resolve=resolver({"internal.example": ["10.0.0.9"]}))


def test_redirect_to_internal_host_is_blocked_and_never_requested():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://internal.example/admin"})

    with pytest.raises(SSRFBlocked):
        _http(handler).request("GET", "https://example.com/start")
    assert seen == ["https://example.com/start"]


def test_redirect_to_literal_metadata_ip_blocked():
    def handler(request):
        return httpx.Response(307, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(SSRFBlocked):
        _http(handler).request("POST", "https://example.com/", content=b"x")


def test_redirect_followed_manually_and_auth_dropped_cross_host():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "example.com":
            return httpx.Response(307, headers={"Location": "https://other.example/next"})
        return httpx.Response(200, json={"ok": True})

    resp = _http(handler).request("GET", "https://example.com/", headers={"Authorization": "Bearer t"})
    assert resp.json() == {"ok": True}
    assert seen == [("https://example.com/", "Bearer t"), ("https://other.example/next", None)]


def test_redirect_loop_is_capped():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://example.com/again"})

    with pytest.raises(PatchQuestError, match="redirects"):
        _http(handler).request("GET", "https://example.com/")


def test_oversized_response_is_refused():
    def handler(request):
        return httpx.Response(200, content=b"x" * (2 << 20))

    with pytest.raises(PatchQuestError, match="too large"):
        _http(handler).request("GET", "https://example.com/")


@pytest.mark.parametrize(("status", "kind"), [
    (400, FailureKind.TOOL_FAILURE), (404, FailureKind.TOOL_FAILURE), (422, FailureKind.TOOL_FAILURE),
    (401, None), (403, None), (429, None), (503, None),
])
def test_failure_statuses_raise(status, kind):
    with pytest.raises((PatchQuestError, httpx.HTTPStatusError)) as err:
        _http(lambda r: httpx.Response(status)).request("GET", "https://example.com/")
    if kind:
        assert err.value.kind is kind
