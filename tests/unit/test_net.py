"""Webhook URL validation.

The service POSTs finished analyses to a caller-supplied URL from inside the
VPC, holding an instance role. Every test here describes a request that must
not be made.
"""
from __future__ import annotations

import ipaddress

import pytest

from labs.core.net import (
    BLOCKED_PORTS,
    WebhookURLError,
    _is_public,
    describe_policy,
    validate_webhook_url,
)


# ------------------------------------------------------------ address rules --
@pytest.mark.parametrize("addr", [
    "127.0.0.1", "127.1.2.3",           # loopback
    "10.0.0.5", "172.16.9.1", "192.168.1.1",   # RFC1918
    "169.254.169.254",                  # cloud instance metadata
    "0.0.0.0",                          # unspecified
    "224.0.0.1",                        # multicast
    "::1",                              # v6 loopback
    "fc00::1",                          # v6 unique local
    "fe80::1",                          # v6 link local
])
def test_non_routable_addresses_are_not_public(addr):
    assert _is_public(ipaddress.ip_address(addr)) is False


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
def test_routable_addresses_are_public(addr):
    assert _is_public(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", [
    "::ffff:127.0.0.1",     # v4-mapped loopback
    "::ffff:169.254.169.254",  # v4-mapped metadata service
    "::ffff:10.0.0.1",      # v4-mapped RFC1918
    "2002:7f00:0001::",     # 6to4 wrapping 127.0.0.1
])
def test_ipv6_wrappers_are_judged_on_the_embedded_v4_address(addr):
    """A private v4 address inside a v6 wrapper is still private.

    `is_global` returns True for some of these, which is exactly the bypass
    the unwrapping in _is_public exists to close.
    """
    assert _is_public(ipaddress.ip_address(addr)) is False


# ------------------------------------------------------------ scheme rules --
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/x",
    "redis://example.com:6379",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url(url)
    assert exc.value.code == "webhook_scheme_not_allowed"


def test_plain_http_is_refused_by_default(monkeypatch):
    monkeypatch.delenv("LABS_WEBHOOK_ALLOW_HTTP", raising=False)
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("http://example.com/hook")
    assert exc.value.code == "webhook_requires_https"


def test_http_can_be_opted_into(monkeypatch):
    monkeypatch.setenv("LABS_WEBHOOK_ALLOW_HTTP", "1")
    target = validate_webhook_url("http://example.com/hook")
    assert target.scheme == "http"


# -------------------------------------------------------------- URL shape --
def test_empty_url_is_refused():
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("   ")
    assert exc.value.code == "webhook_url_empty"


def test_absurdly_long_url_is_refused():
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("https://example.com/" + "a" * 4096)
    assert exc.value.code == "webhook_url_too_long"


def test_embedded_credentials_are_refused():
    """Credentials in a URL end up in logs. Reject rather than strip."""
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("https://user:secret@example.com/hook")
    assert exc.value.code == "webhook_url_has_credentials"


@pytest.mark.parametrize("port", sorted(BLOCKED_PORTS))
def test_service_ports_are_refused(port):
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url(f"https://example.com:{port}/hook")
    assert exc.value.code == "webhook_port_not_allowed"


# ------------------------------------------------------- resolved targets --
@pytest.mark.parametrize("url", [
    "https://localhost/hook",
    "https://127.0.0.1/hook",
    "https://[::1]/hook",
])
def test_hosts_resolving_to_loopback_are_refused(url):
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url(url)
    assert exc.value.code in (
        "webhook_destination_not_allowed", "webhook_host_unresolvable")


def test_metadata_service_is_refused():
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("https://169.254.169.254/latest/meta-data/")
    assert exc.value.code == "webhook_destination_not_allowed"


def test_rejection_does_not_echo_the_resolved_address():
    """Refusals must not turn this endpoint into an internal port scanner.

    If the message named the address a caller could enumerate the VPC by
    submitting hostnames and reading the errors back.
    """
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("https://127.0.0.1/hook")
    assert "127.0.0.1" not in exc.value.message


def test_unresolvable_host_is_refused():
    with pytest.raises(WebhookURLError) as exc:
        validate_webhook_url("https://this-host-does-not-exist.invalid/hook")
    assert exc.value.code == "webhook_host_unresolvable"


def test_private_destinations_can_be_opted_into(monkeypatch):
    """The escape hatch exists for a sidecar receiver and must actually work."""
    monkeypatch.setenv("LABS_WEBHOOK_ALLOW_PRIVATE", "1")
    target = validate_webhook_url("https://127.0.0.1/hook")
    assert target.host == "127.0.0.1"


# ------------------------------------------------------------------ policy --
def test_policy_description_reflects_configuration(monkeypatch):
    monkeypatch.delenv("LABS_WEBHOOK_ALLOW_HTTP", raising=False)
    monkeypatch.delenv("LABS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    policy = describe_policy()
    assert any("https" in p for p in policy)
    assert any("redirects" in p for p in policy)
