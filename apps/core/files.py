"""Private file uploads and authorized downloads (ARCHITECTURE.md D13; audit F-1, F-2, F-3).

Upload pipeline (every step must pass):
  1. size limit enforced by reading the stream (the client-declared size is not trusted);
  2. type sniffed from the content signature; must be allowed for the purpose AND match the extension;
  3. images are decoded and re-encoded (metadata stripped, polyglot payloads destroyed, pixel bombs refused);
  4. PDFs are rejected if they contain active content (JavaScript, launch actions, embedded files, XFA ...),
     including inside compressed streams;
  5. optional antivirus hook (``PRIVATE_FILE_SCANNER``);
  6. stored outside any web root under a random server-generated name; the original name is kept only
     (sanitised) for display.
Downloads are only ever returned by views that have authorized the request (``protected_file_response``).
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
import unicodedata
import warnings
import zlib
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.module_loading import import_string
from PIL import Image

from apps.core.models import FilePurpose, ScanStatus, StoredFile

JPEG, PNG, PDF = "image/jpeg", "image/png", "application/pdf"
SIGNATURES = {JPEG: (b"\xff\xd8\xff",), PNG: (b"\x89PNG\r\n\x1a\n",), PDF: (b"%PDF-",)}
EXTENSIONS = {JPEG: {".jpg", ".jpeg"}, PNG: {".png"}, PDF: {".pdf"}}
CANONICAL_EXTENSION = {JPEG: ".jpg", PNG: ".png", PDF: ".pdf"}

ALLOWED_TYPES = {
    FilePurpose.PROFILE_PHOTO: {JPEG, PNG},
    FilePurpose.REQUEST_ATTACHMENT: {JPEG, PNG, PDF},
    FilePurpose.ANNOUNCEMENT_ATTACHMENT: {JPEG, PNG, PDF},
}
MAX_IMAGE_SIDE = 4096
MAX_IMAGE_PIXELS = 16_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
PDF_ACTIVE_CONTENT = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFile|EmbeddedFiles|RichMedia|XFA|AA|OpenAction|SubmitForm|ImportData|GoToR|GoToE)\b"
)
PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)


def max_size(purpose: str) -> int:
    if purpose == FilePurpose.PROFILE_PHOTO:
        return settings.MAX_PROFILE_PHOTO_BYTES
    return settings.MAX_ATTACHMENT_BYTES


@dataclass
class CleanFile:
    content: bytes
    content_type: str
    original_name: str


def sanitise_filename(name: str, *, fallback: str = "file") -> str:
    """Display-only name: last path component, no control/separator characters, max 150 chars."""
    name = unicodedata.normalize("NFKC", name or "")
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r"[^\w .()\-]", "_", name, flags=re.UNICODE).strip(" .")
    name = re.sub(r"_+", "_", name)
    return (name or fallback)[:150]


def _read_limited(upload, limit: int) -> bytes:
    chunks, total = [], 0
    for chunk in upload.chunks():
        total += len(chunk)
        if total > limit:
            raise ValidationError(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    if total == 0:
        raise ValidationError("The file is empty.")
    return b"".join(chunks)


def sniff_type(data: bytes) -> str | None:
    for content_type, signatures in SIGNATURES.items():
        if any(data.startswith(sig) for sig in signatures):
            return content_type
    return None


def _reencode_image(data: bytes, content_type: str) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)  # near-limit images count as bombs too
            return _decode_and_reencode(data, content_type)
    except ValidationError:
        raise
    except Exception as exc:  # Pillow raises many types (UnidentifiedImageError, DecompressionBombError, ...)
        raise ValidationError("The image could not be processed.") from exc


def _decode_and_reencode(data: bytes, content_type: str) -> bytes:
    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
            raise ValidationError("The image dimensions are too large.")
        image.load()
        if image.format != {JPEG: "JPEG", PNG: "PNG"}[content_type]:
            raise ValidationError("The image content does not match its type.")
        output = io.BytesIO()
        if content_type == JPEG:
            image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)  # no EXIF kept
        else:
            mode = "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
            image.convert(mode).save(output, format="PNG", optimize=True)
        return output.getvalue()


def _check_pdf(data: bytes) -> None:
    if b"%%EOF" not in data[-2048:]:
        raise ValidationError("The PDF file is incomplete or malformed.")
    if b"/Encrypt" in data:
        raise ValidationError("Encrypted PDFs are not accepted.")
    segments = [data]
    for match in PDF_STREAM.finditer(data):
        raw = match.group(1)
        try:
            segments.append(zlib.decompressobj().decompress(raw, 5_000_000))
        except zlib.error:
            continue
    for segment in segments:
        if PDF_ACTIVE_CONTENT.search(segment):
            raise ValidationError("PDFs with scripts, actions or embedded files are not accepted.")


def _scan(data: bytes) -> str:
    scanner_path = getattr(settings, "PRIVATE_FILE_SCANNER", "")
    if not scanner_path:
        return ScanStatus.NOT_SCANNED
    if not import_string(scanner_path)(data):
        raise ValidationError("The file was rejected by the malware scanner.")
    return ScanStatus.CLEAN


def validate_upload(upload, purpose: str) -> CleanFile:
    data = _read_limited(upload, max_size(purpose))
    content_type = sniff_type(data)
    allowed = ALLOWED_TYPES[purpose]
    if content_type not in allowed:
        raise ValidationError("This type of file is not accepted here.")
    original = sanitise_filename(getattr(upload, "name", ""), fallback=f"upload{CANONICAL_EXTENSION[content_type]}")
    extension = ("." + original.rsplit(".", 1)[-1].lower()) if "." in original else ""
    if extension not in EXTENSIONS[content_type]:
        raise ValidationError("The file extension does not match the file content.")
    if content_type in (JPEG, PNG):
        data = _reencode_image(data, content_type)
    else:
        _check_pdf(data)
    return CleanFile(content=data, content_type=content_type, original_name=original)


def store(clean: CleanFile, *, owner, purpose: str) -> StoredFile:
    scan_status = _scan(clean.content)
    now = timezone.now()
    storage_name = f"{purpose.lower()}/{now:%Y/%m}/{secrets.token_hex(16)}{CANONICAL_EXTENSION[clean.content_type]}"
    saved_name = default_storage.save(storage_name, ContentFile(clean.content))
    return StoredFile.objects.create(
        storage_name=saved_name, original_name=clean.original_name, content_type=clean.content_type,
        size=len(clean.content), sha256=hashlib.sha256(clean.content).hexdigest(), owner=owner, purpose=purpose,
        scan_status=scan_status,
    )


def delete_stored_file(stored: StoredFile) -> None:
    name = stored.storage_name
    stored.delete()
    if default_storage.exists(name):
        default_storage.delete(name)


def protected_file_response(stored: StoredFile, *, as_attachment: bool) -> HttpResponse:
    """Serve a file the caller has ALREADY authorized. Never call without an authorization check."""
    if getattr(settings, "PRIVATE_FILES_X_ACCEL_PREFIX", ""):
        response = HttpResponse(content_type=stored.content_type)
        response["X-Accel-Redirect"] = settings.PRIVATE_FILES_X_ACCEL_PREFIX.rstrip("/") + "/" + stored.storage_name
        # RFC 6266/5987 encoding of the (already sanitised) display name - no header injection (audit F-2).
        response["Content-Disposition"] = content_disposition_header(as_attachment, stored.original_name)
    else:
        response = FileResponse(default_storage.open(stored.storage_name, "rb"), as_attachment=as_attachment,
                                filename=stored.original_name, content_type=stored.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response
