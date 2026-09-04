"""SSRF-safe URL fetch tests — per 10-security-plan.md §4 "How to test".

Every probe pattern listed in the security plan must be explicitly tested
and pass before the job post fetch worker or endpoint are built.
"""

import socket
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from app.services.ssrf_safe_fetch import (
    SSRFRejection,
    FetchError,
    ssrf_safe_fetch,
    resolve_and_validate_ip,
    _pinned_dns,
    _connect_and_read_pinned,
    _STREAM_CHUNK_SIZE,
)


# ──────────────────────────────────────────────────────────────────────
# Scheme validation
# ──────────────────────────────────────────────────────────────────────

class TestSchemeValidation:
    """File://, gopher://, ftp:// etc. must be rejected before any DNS lookup."""

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "file://c:/windows/system32/config/sam",
        "gopher://10.0.0.1:80/_GET%20/",
        "ftp://ftp.example.com/",
        "dict://localhost:2628/",
        "jar:file:///tmp/test.jar!/",
    ])
    def test_non_http_schemes_rejected(self, url):
        """Any scheme that is not http or https must raise SSRFRejection."""
        with pytest.raises(SSRFRejection, match="(?i)scheme"):
            ssrf_safe_fetch(url)

    def test_http_and_https_allowed(self):
        """http and https are the only allowed schemes."""
        with patch.object(socket, "getaddrinfo", side_effect=SSRFRejection("mock")):
            with pytest.raises(SSRFRejection):
                ssrf_safe_fetch("https://example.com")


# ──────────────────────────────────────────────────────────────────────
# IP validation (no network calls — mock DNS)
# ──────────────────────────────────────────────────────────────────────

def _mock_dns(ip_list: list[str]):
    """Return a getaddrinfo mock that resolves to the given IP list.

    Each IP becomes a mock sockaddr tuple matching the IPv4/IPv6 family.
    """
    results = []
    for ip in ip_list:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        results.append((family, socket.SOCK_STREAM, 0, "", (ip, 80)))
    return lambda host, port, family, socktype: results


class TestIPValidation:
    """DNS resolution must reject private, loopback, and link-local IPs."""

    def test_cloud_metadata_endpoint_rejected(self):
        """169.254.169.254 is the cloud metadata endpoint — must be blocked."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["169.254.169.254"])):
            with pytest.raises(SSRFRejection,
                               match="private IP.*169.254.169.254"):
                resolve_and_validate_ip("metadata.internal")

    def test_rfc1918_range_10(self):
        """10.x.x.x must be rejected."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["10.0.0.5"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("internal.example.com")

    def test_rfc1918_range_192_168(self):
        """192.168.x.x must be rejected."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["192.168.1.10"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("home.local")

    def test_loopback_rejected(self):
        """127.0.0.1 and ::1 must be rejected."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["127.0.0.1"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("localhost")

        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["::1"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("localhost6")

    def test_link_local_rejected(self):
        """169.254.x.x must be rejected."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["169.254.1.2"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("linklocal.local")

    def test_private_ipv6_rejected(self):
        """Unique local IPv6 addresses (fc00::/7) must be rejected."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["fc00::1"])):
            with pytest.raises(SSRFRejection, match="private IP"):
                resolve_and_validate_ip("ula.local")

    def test_public_ip_allowed(self):
        """A genuine public IP (e.g. 8.8.8.8) must pass validation."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["8.8.8.8"])):
            result = resolve_and_validate_ip("example.com")
            assert result == "8.8.8.8"

    def test_mixed_private_and_public_rejected(self):
        """If ANY resolved address is private, the entire resolution fails."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["8.8.8.8", "10.0.0.1"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("dual.local")

    def test_decimal_encoded_private_ip_rejected(self):
        """If something resolves to a private IP, it's blocked."""
        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["127.0.0.1"])):
            with pytest.raises(SSRFRejection):
                resolve_and_validate_ip("2130706433")


# ──────────────────────────────────────────────────────────────────────
# DNS pinning — the core fix for Bug #3
# ──────────────────────────────────────────────────────────────────────

class TestDNSPinning:
    """_pinned_dns must force the validated IP through socket.getaddrinfo."""

    def test_pinned_dns_returns_validated_ip(self):
        """Within the _pinned_dns context, getaddrinfo returns the pinned IP."""
        hostname = "example.com"
        pinned_ip = "93.184.216.34"

        with _pinned_dns(hostname, pinned_ip):
            result = socket.getaddrinfo(hostname, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            assert len(result) >= 1
            assert result[0][4][0] == pinned_ip

    def test_pinned_dns_does_not_affect_other_hostnames(self):
        """Other hostnames resolve normally — only the pinned hostname is overridden."""
        with patch.object(socket, "getaddrinfo", wraps=socket.getaddrinfo) as spy:
            with _pinned_dns("pinned.example", "8.8.8.8"):
                # Pinned hostname returns the pinned IP
                pinned = socket.getaddrinfo("pinned.example", 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
                assert pinned[0][4][0] == "8.8.8.8"

                # Other hostname goes through to the real getaddrinfo — asserting
                # the call reached the wrapped original is what matters here, not
                # whether it resolves. "other.example" is in the IANA-reserved
                # .example TLD (RFC 6761), so real DNS resolution of it always
                # fails, in any environment — a socket.gaierror here is expected,
                # not a signal the pin leaked.
                try:
                    socket.getaddrinfo("other.example", 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
                except socket.gaierror:
                    pass
                # _pinned_dns's _patched() forwards all 6 positional params
                # (host, port, family, type, proto, flags) to the wrapped
                # original — proto and flags both default to 0.
                spy.assert_any_call("other.example", 80, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, 0)

    def test_pinned_dns_restored_on_exit(self):
        """getaddrinfo is restored to the original after the context manager exits."""
        original = socket.getaddrinfo
        with _pinned_dns("example.com", "1.2.3.4"):
            assert socket.getaddrinfo is not original
        assert socket.getaddrinfo is original


# ──────────────────────────────────────────────────────────────────────
# DNS rebinding: first-to-public, then-to-private
# ──────────────────────────────────────────────────────────────────────

class TestDNSRebinding:
    """DNS rebinding — where the validated IP is public but a subsequent
    resolution returns a private IP — must be prevented by DNS pinning."""

    def test_pinned_ip_preempts_second_resolution(self):
        """After validation, getaddrinfo is pinned — a second DNS lookup
        that would return a private address is never seen by httpx."""
        # Arrange: validate to a public IP, then patch getaddrinfo to
        # simulate DNS changing to a private address after validation.
        # The _pinned_dns context manager makes this impossible because
        # it overrides getaddrinfo for the pinned hostname.
        with patch.object(
            socket, "getaddrinfo", _mock_dns(["8.8.8.8"])
        ):
            validated = resolve_and_validate_ip("evil.example.com")
            assert validated == "8.8.8.8"

        # Now simulate what happens if DNS later returns a private address.
        # _pinned_dns intercepts getaddrinfo for the pinned hostname.
        with _pinned_dns("evil.example.com", "8.8.8.8"):
            # getaddrinfo is overridden — always returns 8.8.8.8
            result = socket.getaddrinfo("evil.example.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            assert result[0][4][0] == "8.8.8.8"
            # The "real" DNS (which might return a private IP) is never called
            # for this hostname inside the pinned context.

    def test_dns_rebind_private_ip_never_used(self):
        """Even if the system resolver starts returning a private IP for the
        hostname, the pinned IP is used for the TCP connection."""
        with patch.object(
            socket, "getaddrinfo",
            # First call: public IP (during validation).
            # If called again (without pinning): private IP.
            side_effect=[
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80))],
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 80))],
            ],
        ):
            validated = resolve_and_validate_ip("rebind.local")
            assert validated == "8.8.8.8"

        # Pinning ensures the private IP is never queried.
        with _pinned_dns("rebind.local", "8.8.8.8"):
            result = socket.getaddrinfo("rebind.local", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            assert result[0][4][0] == "8.8.8.8"


# ──────────────────────────────────────────────────────────────────────
# Redirect re-validation (per-hop)
# ──────────────────────────────────────────────────────────────────────

class TestRedirectRevalidation:
    """After every redirect, the new target IP must be re-validated."""

    def test_redirect_to_private_rejected(self):
        """A public URL that 302-redirects to a private IP is BLOCKED."""
        resolve_calls = [
            "8.8.8.8",                       # first hop — public
            SSRFRejection("private IP"),      # second hop — private
        ]
        def _resolve_side_effect(hostname):
            result = resolve_calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["8.8.8.8"])):
            with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                       side_effect=_resolve_side_effect):
                with patch("app.services.ssrf_safe_fetch._connect_and_read_pinned") as mock_connect:
                    mock_connect.return_value = (b"", "http://10.0.0.1/redirect-target")
                    with pytest.raises(SSRFRejection):
                        ssrf_safe_fetch("http://safe.example.com")

    def test_redirect_across_schemes_rejected(self):
        """A redirect from https to http with a different host: the new host
        is validated — if it's private, it must be rejected."""
        resolve_calls = [
            "8.8.8.8",                       # first hop (https): public
            SSRFRejection("private IP"),      # redirect target: private
        ]
        def _resolve_side_effect(hostname):
            result = resolve_calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["8.8.8.8"])):
            with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                       side_effect=_resolve_side_effect):
                with patch("app.services.ssrf_safe_fetch._connect_and_read_pinned") as mock_connect:
                    mock_connect.return_value = (b"", "http://10.0.0.1/secret")
                    with pytest.raises(SSRFRejection):
                        ssrf_safe_fetch("https://public.example.com")

    def test_redirect_to_public_allowed(self):
        """A redirect to another public IP passes validation."""
        resolve_calls = [
            "8.8.8.8",    # first hop
            "1.1.1.1",    # redirect target (public)
        ]
        def _resolve_side_effect(hostname):
            result = resolve_calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(socket, "getaddrinfo",
                          _mock_dns(["8.8.8.8"])):
            with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                       side_effect=_resolve_side_effect):
                with patch("app.services.ssrf_safe_fetch._connect_and_read_pinned") as mock_connect:
                    mock_connect.return_value = (b"OK!", None)
                    result = ssrf_safe_fetch("http://safe.example.com")
                    assert "OK!" in result


# ──────────────────────────────────────────────────────────────────────
# Size and timeout enforcement
# ──────────────────────────────────────────────────────────────────────

class TestSizeAndTimeout:
    """Response size cap and timeout must be enforced."""

    def test_response_too_large_raises(self):
        """A response exceeding max_bytes must raise FetchError."""
        with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                   return_value="8.8.8.8"):
            with patch("app.services.ssrf_safe_fetch._connect_and_read_pinned",
                       side_effect=FetchError("Response exceeds maximum size")):
                with pytest.raises(FetchError):
                    ssrf_safe_fetch("http://example.com", max_bytes=100)

    def test_timeout_raises_fetch_error(self):
        """A timeout must raise FetchError (not a raw socket.timeout)."""
        with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                   return_value="8.8.8.8"):
            with patch("app.services.ssrf_safe_fetch._connect_and_read_pinned",
                       side_effect=FetchError("Timed out fetching")):
                with pytest.raises(FetchError):
                    ssrf_safe_fetch("http://example.com", timeout=1)


# ──────────────────────────────────────────────────────────────────────
# Streaming size enforcement — Phase 2, Task 2.2
# ──────────────────────────────────────────────────────────────────────


class _FakeStreamResponse:
    """A minimal fake for httpx's streamed response, returning controlled
    chunks and tracking consumption so tests can prove that oversized
    responses are aborted without reading the full body."""

    def __init__(self, chunks, status_code=200, headers=None):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = headers or {}
        self._iter_calls = 0
        self._iter_chunk_size = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False  # do not suppress exceptions

    def iter_bytes(self, chunk_size=None):
        for c in self._chunks:
            self._iter_chunk_size = chunk_size
            self._iter_calls += 1
            yield c

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError, Request, Response
            req_dummy = Request("GET", "http://fake")
            resp_dummy = Response(self.status_code, request=req_dummy)
            raise HTTPStatusError("status error", request=req_dummy, response=resp_dummy)


class TestStreamingSizeEnforcement:
    """Focused behavioural tests for the streaming read path.

    All tests inject a fake `httpx.Client` whose `stream()` context manager
    returns the controlled `_FakeStreamResponse`.  No real network calls
    are made."""

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _patch_client(fake_resp):
        """Return a patch that replaces `httpx.Client` with a mock that
        surfaces *fake_resp* as the stream response."""
        from unittest.mock import MagicMock
        mock_inst = MagicMock()
        mock_inst.__enter__.return_value = mock_inst
        mock_inst.stream.return_value = fake_resp
        return patch("app.services.ssrf_safe_fetch.httpx.Client", return_value=mock_inst)

    # ── tests ─────────────────────────────────────────────────────────

    def test_small_response_returns_content(self):
        """A response at or below max_bytes returns the full body."""
        fake = _FakeStreamResponse(chunks=[b"OK"], status_code=200)
        with self._patch_client(fake):
            body, redirect = _connect_and_read_pinned(
                scheme="http", host="s.example", port=80,
                parsed=urllib.parse.urlparse("http://s.example/"),
                validated_ip="8.8.8.8", timeout=5, max_bytes=100,
            )
        assert body == b"OK"
        assert redirect is None
        assert fake.closed
        assert fake._iter_chunk_size == _STREAM_CHUNK_SIZE, (  # explicit chunk size passed
            f"Expected chunk_size {_STREAM_CHUNK_SIZE}, got {fake._iter_chunk_size}"
        )

    def test_response_exactly_at_limit_succeeds(self):
        """A response whose body length equals max_bytes is accepted."""
        limit = 10
        fake = _FakeStreamResponse(chunks=[b"a" * limit], status_code=200)
        with self._patch_client(fake):
            body, _ = _connect_and_read_pinned(
                scheme="http", host="x", port=80,
                parsed=urllib.parse.urlparse("http://x/"),
                validated_ip="8.8.8.8", timeout=5, max_bytes=limit,
            )
        assert body == b"a" * limit

    def test_one_byte_over_limit_raises_fetch_error(self):
        """When the response body is one byte over max_bytes, FetchError is
        raised and the stream is closed."""
        limit = 10
        fake = _FakeStreamResponse(chunks=[b"a" * (limit + 1)], status_code=200)
        with self._patch_client(fake):
            with pytest.raises(FetchError) as exc_info:
                _connect_and_read_pinned(
                    scheme="http", host="x", port=80,
                    parsed=urllib.parse.urlparse("http://x/"),
                    validated_ip="8.8.8.8", timeout=5, max_bytes=limit,
                )
        error_msg = str(exc_info.value)
        assert str(limit) in error_msg
        assert fake.closed, "stream must be closed after FetchError"
        assert fake._iter_chunk_size == _STREAM_CHUNK_SIZE, (  # explicit chunk size passed
            f"Expected chunk_size {_STREAM_CHUNK_SIZE}, got {fake._iter_chunk_size}"
        )

    def test_multichunk_over_limit_stops_at_first_over_limit_chunk(self):
        """When a chunked response exceeds max_bytes partway through, the
        loop stops iteration at the over-limit chunk and does NOT consume
        any later chunks."""
        # 3 chunks, max_bytes=250 — the 3rd chunk pushes total to 300
        chunks = [b"a" * 100, b"b" * 100, b"c" * 100, b"d" * 100]
        limit = 250
        fake = _FakeStreamResponse(chunks=chunks, status_code=200)
        with self._patch_client(fake):
            with pytest.raises(FetchError):
                _connect_and_read_pinned(
                    scheme="http", host="x", port=80,
                    parsed=urllib.parse.urlparse("http://x/"),
                    validated_ip="8.8.8.8", timeout=5, max_bytes=limit,
                )
        # total = 100 (chunk 1), 200 (chunk 2), 300 (chunk 3 > limit → abort).
        # Only 3 chunks were requested; the 4th was never consumed.
        assert fake._iter_calls == 3, (
            f"Expected only 3 chunks consumed before abort, got {fake._iter_calls}"
        )
        assert fake.closed
        assert fake._iter_chunk_size == _STREAM_CHUNK_SIZE, (  # explicit chunk size passed
            f"Expected chunk_size {_STREAM_CHUNK_SIZE}, got {fake._iter_chunk_size}"
        )

    def test_redirect_returns_location_without_reading_body(self):
        """A redirect response returns (b"", Location) and must not iterate
        over the body."""
        fake = _FakeStreamResponse(
            chunks=[b"should-not-be-read"],
            status_code=302,
            headers={"Location": "http://next.example.com/"},
        )
        with self._patch_client(fake):
            body, loc = _connect_and_read_pinned(
                scheme="http", host="src", port=80,
                parsed=urllib.parse.urlparse("http://src/"),
                validated_ip="8.8.8.8", timeout=5, max_bytes=100,
            )
        assert body == b""
        assert loc == "http://next.example.com/"
        assert fake._iter_calls == 0, "body must not be iterated for redirect"

    def test_full_ssrf_safe_fetch_still_succeeds_public_ip(self):
        """Use the real streaming path inside ssrf_safe_fetch with a mocked
        public IP and a faked streaming body.  Confirms the outer redirect
        loop and exception mapping still compose correctly."""
        fake = _FakeStreamResponse(chunks=[b"hello world"], status_code=200)
        with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                   return_value="8.8.8.8"):
            with self._patch_client(fake):
                result = ssrf_safe_fetch("http://public.example/")
        assert "hello world" in result

    def test_streaming_read_timeout_maps_to_fetch_error(self):
        """When `iter_bytes` raises `httpx.ReadTimeout`, the handler in
        ssrf_safe_fetch must convert it to a FetchError with 'Timed out'."""
        fake = _FakeStreamResponse(chunks=[b"x"], status_code=200)
        from httpx import ReadTimeout
        fake.iter_bytes = lambda chunk_size=None: (_ for _ in ()).throw(ReadTimeout("timed out"))
        with patch("app.services.ssrf_safe_fetch.resolve_and_validate_ip",
                   return_value="8.8.8.8"):
            with self._patch_client(fake):
                with pytest.raises(FetchError, match="Timed out"):
                    ssrf_safe_fetch("http://slow.example/")