"""Client IP must not be spoofable via X-Forwarded-For (audit I-1; rate limiting & audit integrity)."""

from django.test import RequestFactory, override_settings

from apps.core.net import get_client_ip

rf = RequestFactory()


def _req(remote="203.0.113.10", xff=None):
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return rf.get("/", **extra)


@override_settings(TRUSTED_PROXY_COUNT=0)
def test_forwarded_header_ignored_without_trusted_proxy():
    assert get_client_ip(_req(xff="1.2.3.4")) == "203.0.113.10"


@override_settings(TRUSTED_PROXY_COUNT=1)
def test_with_one_proxy_uses_rightmost_entry_not_client_supplied_left_entries():
    # Client forged "1.2.3.4"; the trusted proxy appended the real peer 198.51.100.7.
    assert get_client_ip(_req(remote="10.0.0.2", xff="1.2.3.4, 198.51.100.7")) == "198.51.100.7"


@override_settings(TRUSTED_PROXY_COUNT=2)
def test_with_two_proxies_uses_second_from_right():
    assert get_client_ip(_req(remote="10.0.0.3", xff="6.6.6.6, 198.51.100.7, 10.0.0.2")) == "198.51.100.7"


@override_settings(TRUSTED_PROXY_COUNT=1)
def test_garbage_forwarded_value_falls_back_to_remote_addr():
    assert get_client_ip(_req(remote="10.0.0.2", xff="not-an-ip")) == "10.0.0.2"
    assert get_client_ip(_req(remote="10.0.0.2", xff="")) == "10.0.0.2"


def test_invalid_remote_addr_returns_none():
    assert get_client_ip(_req(remote="<script>")) is None


def test_legacy_utils_wrapper_delegates():
    from apps.core.utils import get_client_ip as legacy

    with override_settings(TRUSTED_PROXY_COUNT=0):
        assert legacy(_req(xff="9.9.9.9")) == "203.0.113.10"
