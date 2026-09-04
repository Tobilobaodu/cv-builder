"""SSRF-safe URL fetch utility.

Per 10-security-plan.md §4: validates scheme, resolved IP, redirect chain,
timeout, and response size before fetching a user-supplied URL.

Uses httpx with DNS pinning to eliminate the DNS-rebinding TOCTOU gap:
the validated IP is pinned before the TCP connection, so a second DNS
resolution (which could return a different answer) never happens.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket as std_socket
import urllib.parse
from typing import Tuple

import httpx

from app.core.metrics import SSRF_REJECTED_COUNTER
from app.core.metrics_push import push_worker_metrics

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# Schemes we allow — everything else is rejected before DNS resolution.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Private / reserved ranges.  We block these regardless of how the IP
# was encoded (dotted-decimal, decimal integer, hex, octal, URI-encoded).
_PRIVATE_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),     # loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local (includes cloud metadata)
    ipaddress.IPv4Network("0.0.0.0/8"),        # "This host on this network"
    ipaddress.IPv6Network("::1/128"),           # IPv6 loopback
    ipaddress.IPv6Network("fe80::/10"),         # IPv6 link-local
    ipaddress.IPv6Network("fc00::/7"),          # Unique local (private IPv6)
]

# Default limits
_DEFAULT_TIMEOUT_SECONDS = 15
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_STREAM_CHUNK_SIZE = 64 * 1024


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


class SSRFRejection(ValueError):
    """Raised when a URL fails SSRF validation.

    Increments SSRF_REJECTED_COUNTER on construction — every rejection path
    raises exactly one of these, so this is the single chokepoint that keeps
    the §10 SSRF-probing alert wired regardless of which validation rule fired.
    """

    def __init__(self, message: str):
        SSRF_REJECTED_COUNTER.inc()
        push_worker_metrics("worker_job_fetch")
        super().__init__(message)


class FetchError(ValueError):
    """Raised when a validated URL cannot be fetched (timeout, size, HTTP error)."""


def ssrf_safe_fetch(
    url: str,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> str:
    """Fetch a URL with SSRF-safe validation at every hop.

    1. Validate the scheme.
    2. Resolve the hostname and validate the IP is public.
    3. Connect with timeout, pinning the validated IP (no DNS rebinding).
    4. Follow redirects, re-validating the target IP at every hop.
    5. Enforce a maximum response size.

    Args:
        url: The user-supplied URL to fetch.
        timeout: Connection + read timeout in seconds.
        max_bytes: Maximum response body size in bytes.

    Returns:
        The response body as a decoded UTF-8 string.

    Raises:
        SSRFRejection: If the URL or any redirect target is unsafe.
        FetchError: If the URL cannot be fetched for non-SSRF reasons.
    """
    remaining_hops = 5  # max redirect chain depth
    current_url = url

    while remaining_hops > 0:
        remaining_hops -= 1

        parsed = _validate_and_parse_url(current_url)
        host = parsed.hostname
        if not host:
            raise SSRFRejection(f"URL '{current_url}' has no hostname.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        scheme = parsed.scheme

        # Resolve and validate the IP — this is the single DNS resolution
        # that gates the connection.  The validated IP is then pinned through
        # _connect_and_read_pinned() so the TCP connection never re-resolves.
        validated_ip = resolve_and_validate_ip(host)

        try:
            body, redirect_url = _connect_and_read_pinned(
                scheme, host, port, parsed, validated_ip, timeout, max_bytes
            )
        except (TimeoutError, httpx.TimeoutException):
            raise FetchError(
                f"Timed out fetching {scheme}://{host}:{port} after {timeout}s"
            )
        except (ConnectionError, httpx.ConnectError) as exc:
            raise FetchError(f"Cannot connect to {scheme}://{host}:{port}: {exc}")
        except httpx.HTTPStatusError as exc:
            raise FetchError(
                f"HTTP {exc.response.status_code} fetching {scheme}://{host}{parsed.path or '/'}"
            )

        if redirect_url is not None:
            # Normalise relative redirects against the _original_ URL so
            # a relative Location on a redirect chain is resolved correctly.
            current_url = urllib.parse.urljoin(current_url, redirect_url)
            continue

        # No redirect — decode and return
        try:
            return body.decode("utf-8", errors="replace")
        except Exception as exc:
            raise FetchError(f"Failed to decode response body: {exc}")

    raise FetchError("Too many redirects (max 5)")


def resolve_and_validate_ip(hostname: str) -> str:
    """Resolve *hostname* and return its IP if it is a public, routable address.

    Raises SSRFRejection for any private, loopback, link-local, or
    otherwise non-public address.
    """
    try:
        # getaddrinfo returns *all* addresses; check every one.
        addrinfo = std_socket.getaddrinfo(hostname, None, std_socket.AF_UNSPEC, std_socket.SOCK_STREAM)
    except std_socket.gaierror as exc:
        raise SSRFRejection(f"Cannot resolve hostname '{hostname}': {exc}")

    seen = set()
    last_public_ip = None

    for family, _, _, _, sockaddr in addrinfo:
        ip_str = str(sockaddr[0])
        # Strip scope ID from IPv6 addresses (e.g. "fe80::1%eth0")
        ip_str = ip_str.split("%")[0]

        if ip_str in seen:
            continue
        seen.add(ip_str)

        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFRejection(f"Unparseable IP address for '{hostname}': {ip_str}")

        if _is_private(addr):
            raise SSRFRejection(
                f"Hostname '{hostname}' resolves to private IP {ip_str} "
                f"(prohibited per SSRF controls)"
            )

        last_public_ip = ip_str

    if last_public_ip is None:
        raise SSRFRejection(f"No routable IP address found for '{hostname}'")

    return last_public_ip


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


def _validate_and_parse_url(url: str) -> urllib.parse.ParseResult:
    """Parse *url* and reject anything with a non-whitelisted scheme."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFRejection(
            f"Scheme '{parsed.scheme}' is not allowed. "
            f"Only http and https are supported."
        )
    if not parsed.hostname:
        raise SSRFRejection(f"URL '{url}' has no parseable hostname.")
    return parsed


def _is_private(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *addr* is in any private / reserved range."""
    if addr.is_loopback or addr.is_link_local or addr.is_private:
        return True
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            return True
    return False


@contextlib.contextmanager
def _pinned_dns(hostname: str, ip: str):
    """Temporarily resolve *hostname* to the validated *ip*.

    Celery workers use the prefork pool (process-based concurrency) so
    patching `socket.getaddrinfo` is process-local and not visible to
    other worker processes.  The patch is always reverted on exit.
    """
    original_getaddrinfo = std_socket.getaddrinfo

    def _patched(host, port, family=0, type=0, proto=0, flags=0):
        if host == hostname:
            addr_family = std_socket.AF_INET6 if ":" in ip else std_socket.AF_INET
            return [(addr_family, std_socket.SOCK_STREAM, 6, "", (ip, port))]
        return original_getaddrinfo(host, port, family, type, proto, flags)

    std_socket.getaddrinfo = _patched
    try:
        yield
    finally:
        std_socket.getaddrinfo = original_getaddrinfo


def _connect_and_read_pinned(
    scheme: str,
    host: str,
    port: int,
    parsed: urllib.parse.ParseResult,
    validated_ip: str,
    timeout: int,
    max_bytes: int,
) -> Tuple[bytes, str | None]:
    """Connect to *validated_ip* while presenting *host* for SNI and Host header.

    Uses httpx with DNS pinning so that the TCP connection goes to the
    pre-validated IP.  httpx handles TLS SNI and the Host header
    correctly because it sees the original hostname in the URL.

    Returns (body_bytes, redirect_url_or_None).
    """
    # Reconstruct the URL using the original hostname (not the IP) so
    # httpx sets SNI and Host correctly.
    path_qs = parsed.path or "/"
    if parsed.query:
        path_qs += f"?{parsed.query}"
    url = f"{scheme}://{host}{path_qs}"

    with _pinned_dns(host, validated_ip):
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "CV-Tailoring/1.0"},
        ) as client:
            with client.stream("GET", url) as response:
                # Handle redirects — the caller re-validates the target hop.
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    return b"", location

                if response.status_code >= 400:
                    response.raise_for_status()  # raises HTTPStatusError

                # Read the body incrementally and abort as soon as the
                # cumulative size exceeds the cap, so an oversized response
                # never fully accumulates in worker memory.
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=_STREAM_CHUNK_SIZE):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(
                            f"Response exceeds maximum size of {max_bytes} bytes "
                            f"(aborted at {total} bytes while streaming)."
                        )
                    chunks.append(chunk)
                return b"".join(chunks), None