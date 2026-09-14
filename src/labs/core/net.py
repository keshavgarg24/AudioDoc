"""Outbound request safety.

The API accepts a caller-supplied `webhook_url` and POSTs the finished analysis
to it. Without a guard that is a server-side request forgery primitive: the
caller chooses a URL and this service, which sits inside a VPC with an instance
role, makes the request on their behalf.

On AWS the sharpest edge is the instance metadata service at 169.254.169.254.
IMDSv2 requires a PUT to obtain a token so a plain POST cannot lift credentials
directly, but IMDSv1 (still enabled on many AMIs) does not, and the wider
problem is unchanged: link-local, loopback, and RFC1918 addresses all reach
things that are only reachable because this process is inside the perimeter -
internal load balancers, admin ports, databases, other services' health and
management endpoints.

What is enforced here:

  - scheme must be http or https; https unless LABS_WEBHOOK_ALLOW_HTTP is set
  - the hostname is resolved, and *every* address it resolves to must be a
    global unicast address. Checking the literal string is not enough: a
    hostname under the caller's control can point at 127.0.0.1.
  - the resolved address is pinned and reused for the actual connection, so a
    name that resolves differently between the check and the request (DNS
    rebinding) cannot slip past.
  - redirects are not followed, since a permitted host can 302 to a blocked one.

The pinning is what makes this a real control rather than a speed bump. A
check-then-connect that re-resolves is a TOCTOU bug with a well known exploit.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")

# Ports that are never a webhook receiver and frequently are something else
# worth reaching from inside a VPC. Blocking these costs nothing real.
BLOCKED_PORTS = frozenset({
    22,     # ssh
    23,     # telnet
    25, 465, 587,  # smtp - open relays / mail injection
    3306,   # mysql
    5432,   # postgres
    6379,   # redis
    9200,   # elasticsearch
    11211,  # memcached
    27017,  # mongodb
})


class WebhookURLError(ValueError):
    """The supplied webhook URL is not one this service will call."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedTarget:
    """A validated URL plus the address it is pinned to."""
    url: str
    host: str
    ip: str
    port: int
    scheme: str


def _allow_http() -> bool:
    return os.environ.get(
        "LABS_WEBHOOK_ALLOW_HTTP", "").strip().lower() in ("1", "true", "yes", "on")


def _allow_private() -> bool:
    """Escape hatch for deployments whose receiver genuinely is internal.

    Off by default and deliberately awkward to turn on. A service mesh sidecar
    on 127.0.0.1 is the only legitimate case we have seen.
    """
    return os.environ.get(
        "LABS_WEBHOOK_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes", "on")


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    """True only for addresses that route on the public internet.

    `is_global` alone is not sufficient: it is False for several ranges we care
    about but True for some IPv6 transition addresses that embed a private v4
    target, so those are unwrapped first.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        # ::ffff:127.0.0.1 and 64:ff9b::/96 both carry a v4 address that must
        # be judged on its own merits rather than the wrapper's.
        if ip.ipv4_mapped:
            return _is_public(ip.ipv4_mapped)
        if ip.sixtofour:
            return _is_public(ip.sixtofour)
        if ip.teredo:
            return all(_is_public(a) for a in ip.teredo)

    return not (
        ip.is_private          # RFC1918, and ::1 / fc00::/7 on v6
        or ip.is_loopback
        or ip.is_link_local    # 169.254.0.0/16 - IMDS lives here
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        # UnicodeError, not just gaierror: getaddrinfo runs the hostname
        # through the IDNA codec first, and a name with an empty label
        # ("https://...", "https://.") fails there instead. That is a
        # UnicodeEncodeError, it is not a subclass of gaierror, and left
        # uncaught it leaves this function as an unhandled exception - which
        # the caller turns into a 500 on what is plainly a bad request.
        raise WebhookURLError(
            "webhook_host_unresolvable",
            f"The webhook host '{host}' could not be resolved.") from exc
    # Preserve order (getaddrinfo returns them in the OS's preferred order) but
    # drop duplicates, which are common when a name has both A and AAAA records
    # pointing through the same path.
    seen, out = set(), []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def validate_webhook_url(raw: str) -> ResolvedTarget:
    """Vet a caller-supplied webhook URL, or raise WebhookURLError.

    Returns the target pinned to a specific address. Call this at submission
    time so the caller gets a 422 they can act on, rather than at delivery time
    where the only outcome is a log line they never see.
    """
    if not raw or not raw.strip():
        raise WebhookURLError("webhook_url_empty", "The webhook URL is empty.")

    raw = raw.strip()
    if len(raw) > 2048:
        raise WebhookURLError("webhook_url_too_long",
                              "The webhook URL is longer than 2048 characters.")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise WebhookURLError("webhook_url_malformed",
                              "The webhook URL could not be parsed.") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise WebhookURLError(
            "webhook_scheme_not_allowed",
            f"The webhook URL must use http or https, not '{scheme or 'none'}'.")

    if scheme == "http" and not _allow_http():
        raise WebhookURLError(
            "webhook_requires_https",
            "The webhook URL must use https. Analysis results are sent in the "
            "request body and http would expose them in transit.")

    # Credentials in the URL are almost always a mistake, and they end up in
    # logs. Reject rather than silently strip.
    if parsed.username or parsed.password:
        raise WebhookURLError(
            "webhook_url_has_credentials",
            "The webhook URL must not contain a username or password. Use a "
            "token in the path or verify the request signature instead.")

    host = parsed.hostname
    if not host:
        raise WebhookURLError("webhook_url_no_host",
                              "The webhook URL has no host.")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise WebhookURLError("webhook_url_bad_port",
                              "The webhook URL has an invalid port.") from exc

    if port in BLOCKED_PORTS:
        raise WebhookURLError(
            "webhook_port_not_allowed",
            f"Port {port} is not a permitted webhook destination.")

    addresses = _resolve(host, port)
    if not addresses:
        raise WebhookURLError("webhook_host_unresolvable",
                              f"The webhook host '{host}' resolved to no addresses.")

    permissive = _allow_private()
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise WebhookURLError(
                "webhook_host_unresolvable",
                f"The webhook host '{host}' resolved to an unusable address."
            ) from exc
        if not _is_public(ip) and not permissive:
            # The specific address is deliberately not echoed back: that turns
            # this endpoint into an internal-network scanner with a clean
            # oracle. The caller learns their URL was rejected, nothing more.
            log.warning("Rejected webhook to non-public address (host=%s)", host)
            raise WebhookURLError(
                "webhook_destination_not_allowed",
                "The webhook URL must resolve to a public internet address.")

    return ResolvedTarget(url=raw, host=host, ip=addresses[0],
                          port=port, scheme=scheme)


def describe_policy() -> Tuple[str, ...]:
    """Human readable summary of what is enforced, for docs and /v1/health."""
    policy = ["https required" if not _allow_http() else "http permitted",
              "must resolve to a public address"]
    if _allow_private():
        policy = ["https required" if not _allow_http() else "http permitted",
                  "private addresses permitted (LABS_WEBHOOK_ALLOW_PRIVATE)"]
    policy.append("redirects not followed")
    return tuple(policy)
