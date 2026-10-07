"""Stock Django admin and uploaded media must not be exposed (audit A-2, F-1)."""

import importlib

import pytest
from django.test import override_settings
from django.urls import Resolver404, clear_url_caches, resolve


def _reload_urls():
    import portal_config.urls

    clear_url_caches()
    importlib.reload(portal_config.urls)


@pytest.fixture(autouse=True)
def restore_urls():
    yield
    _reload_urls()


@pytest.mark.django_db
def test_django_admin_not_mounted_by_default(client):
    _reload_urls()
    assert client.get("/django-admin/").status_code == 404
    assert client.get("/django-admin/login/").status_code == 404


@pytest.mark.parametrize("debug", [False, True])
def test_admin_flag_alone_never_mounts_admin_without_debug(debug):
    with override_settings(DEBUG=debug, DJANGO_ADMIN_ENABLED=True):
        _reload_urls()
        if debug:
            assert resolve("/django-admin/")  # dev convenience only
        else:
            with pytest.raises(Resolver404):
                resolve("/django-admin/")


@pytest.mark.parametrize("debug", [False, True])
def test_uploaded_media_is_never_served_publicly(debug, settings):
    with override_settings(DEBUG=debug):
        _reload_urls()
        for url in (settings.MEDIA_URL + "attachments/x.pdf", "/media/attachments/x.pdf"):
            with pytest.raises(Resolver404):
                resolve(url)
