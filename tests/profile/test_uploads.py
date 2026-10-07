"""Upload pipeline: type sniffing, re-encoding, active-content rejection, size limits (T11; audit F-2, F-3)."""

import io
import zlib

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.core import files
from apps.core.models import FilePurpose

PHOTO = FilePurpose.PROFILE_PHOTO
ATTACHMENT = FilePurpose.REQUEST_ATTACHMENT


def image_bytes(fmt="PNG", size=(64, 48), exif=False) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", size, (200, 30, 30))
    if exif:
        data = Image.Exif()
        data[0x010F] = "SpyCam Inc"  # Make
        data[0x8825] = {2: (1.0, 2.0, 3.0)}  # GPS info
        image.save(buffer, format=fmt, exif=data)
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name, data, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def pdf(body: bytes) -> bytes:
    return b"%PDF-1.7\n" + body + b"\n%%EOF\n"


def test_png_and_jpeg_are_accepted_and_reencoded():
    for fmt, name in (("PNG", "me.png"), ("JPEG", "me.JPG")):
        clean = files.validate_upload(upload(name, image_bytes(fmt)), PHOTO)
        assert clean.content_type in (files.PNG, files.JPEG)
        Image.open(io.BytesIO(clean.content)).verify()


def test_exif_metadata_is_stripped():
    raw = image_bytes("JPEG", exif=True)
    assert b"SpyCam" in raw
    clean = files.validate_upload(upload("photo.jpg", raw), PHOTO)
    assert b"SpyCam" not in clean.content and b"Exif" not in clean.content


def test_polyglot_payload_does_not_survive():
    payload = b"<?php system($_GET['c']); ?><script>alert(1)</script>"
    clean = files.validate_upload(upload("cat.png", image_bytes("PNG") + payload), PHOTO)
    assert b"<?php" not in clean.content and b"<script" not in clean.content


@pytest.mark.parametrize(
    "name, data",
    [
        ("shell.php", b"<?php echo 1; ?>"),
        ("page.html", b"<html><script>alert(1)</script></html>"),
        ("vector.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'),
        ("fake.png", b"\x89PNG\r\n\x1a\n" + b"not really an image"),
        ("program.exe", b"MZ\x90\x00" + b"\x00" * 100),
    ],
)
def test_non_images_are_rejected(name, data):
    with pytest.raises(ValidationError):
        files.validate_upload(upload(name, data, "image/png"), PHOTO)


def test_extension_must_match_content():
    with pytest.raises(ValidationError):
        files.validate_upload(upload("photo.jpg", image_bytes("PNG")), PHOTO)
    with pytest.raises(ValidationError):
        files.validate_upload(upload("photo.png.php", image_bytes("PNG")), PHOTO)


def test_declared_content_type_is_ignored():
    with pytest.raises(ValidationError):
        files.validate_upload(upload("doc.png", b"%PDF-1.4 fake", "image/png"), PHOTO)


def test_size_limit_is_enforced_on_the_content(settings):
    settings.MAX_PROFILE_PHOTO_BYTES = 1000
    with pytest.raises(ValidationError):
        files.validate_upload(upload("big.png", image_bytes("PNG", size=(400, 400)) + b"\x00" * 2000), PHOTO)


def test_pixel_bomb_is_rejected():
    with pytest.raises(ValidationError):
        files.validate_upload(upload("bomb.png", image_bytes("PNG", size=(5000, 4000))), PHOTO)


def test_pdf_is_not_a_valid_photo_but_is_a_valid_attachment():
    clean_pdf = pdf(b"1 0 obj << /Type /Catalog >> endobj")
    with pytest.raises(ValidationError):
        files.validate_upload(upload("doc.pdf", clean_pdf), PHOTO)
    assert files.validate_upload(upload("doc.pdf", clean_pdf), ATTACHMENT).content_type == files.PDF


@pytest.mark.parametrize("marker", [b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/XFA"])
def test_pdf_active_content_is_rejected(marker):
    with pytest.raises(ValidationError):
        files.validate_upload(upload("evil.pdf", pdf(b"1 0 obj << " + marker + b" (x) >> endobj")), ATTACHMENT)


def test_pdf_active_content_hidden_in_a_compressed_stream_is_rejected():
    hidden = zlib.compress(b"<< /S /JavaScript /JS (app.alert(1)) >>")
    body = b"2 0 obj << /Length %d /Filter /FlateDecode >>\nstream\n" % len(hidden) + hidden + b"\nendstream endobj"
    with pytest.raises(ValidationError):
        files.validate_upload(upload("sneaky.pdf", pdf(body)), ATTACHMENT)


def test_filenames_are_sanitised_for_display():
    assert files.sanitise_filename("../../etc/passwd.png") == "passwd.png"
    assert files.sanitise_filename("..\\..\\boot.ini") == "boot.ini"
    assert "\r" not in files.sanitise_filename('evil"\r\nX-Injected: 1.png')
    assert files.sanitise_filename("") == "file"


@pytest.mark.django_db
def test_stored_file_gets_random_name_outside_web_roots(settings, tmp_path):
    from tests import factories as f

    settings.MEDIA_ROOT = tmp_path
    owner = f.user()
    stored = files.store(files.validate_upload(upload("../../../x.png", image_bytes("PNG")), PHOTO), owner=owner,
                         purpose=PHOTO)
    assert ".." not in stored.storage_name and "x.png" not in stored.storage_name
    assert stored.original_name == "x.png" and len(stored.sha256) == 64
    assert (tmp_path / stored.storage_name).exists()
    response = files.protected_file_response(stored, as_attachment=True)
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"].startswith("attachment") and "\n" not in response["Content-Disposition"]
    assert "sandbox" in response["Content-Security-Policy"]
