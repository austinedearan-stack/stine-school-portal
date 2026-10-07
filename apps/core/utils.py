import os
import uuid

from django.core.exceptions import ValidationError

# Permitted extensions and MIME signatures
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
ALLOWED_DOCUMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}

ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png'}
ALLOWED_DOCUMENT_MIMES = {'application/pdf', 'image/jpeg', 'image/png'}

MAX_PROFILE_PHOTO_SIZE = 2 * 1024 * 1024  # 2MB
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024     # 5MB

# Magic byte signatures
MAGIC_SIGNATURES = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'%PDF-': 'application/pdf',
}

# Dangerous executable / script patterns to block
DANGEROUS_SIGNATURES = [
    b'MZ',                   # Windows EXE / DLL
    b'\x7fELF',              # Linux ELF executable
    b'<?php',                # PHP script
    b'<script',              # Inline HTML script
    b'eval(',                # Script eval
    b'#!/bin/',              # Unix shell script
    b'#!/usr/bin/',          # Unix shell script
]


def secure_filename(filename: str, prefix: str = 'file') -> str:
    """
    Generates a secure server-side UUID filename, completely discarding untrusted user filenames.
    Prevents path traversal and shell injection attacks.
    """
    ext = os.path.splitext(filename)[1].lower()
    return f"{prefix}_{uuid.uuid4().hex}{ext}"


def get_client_ip(request):
    """Backward-compatible wrapper; see apps.core.net.get_client_ip (trusted-proxy aware)."""
    from apps.core.net import get_client_ip as _get_client_ip

    return _get_client_ip(request)


def validate_file_security(uploaded_file, allowed_extensions, allowed_mimes, max_size_bytes):
    """
    Enforces strict file upload validation:
    1. Maximum file size check
    2. Whitelisted extension check
    3. Stated MIME type check
    4. Magic byte signature inspection
    5. Disallowed executable inspection
    """
    # 1. Size check
    if uploaded_file.size > max_size_bytes:
        raise ValidationError(f"File size exceeds maximum allowed limit of {max_size_bytes // (1024*1024)}MB.")

    # 2. Extension check
    _, ext = os.path.splitext(uploaded_file.name)
    ext = ext.lower()
    if ext not in allowed_extensions:
        raise ValidationError(f"Disallowed file extension '{ext}'. Allowed extensions: {', '.join(allowed_extensions)}.")

    # 3. MIME type check
    content_type = getattr(uploaded_file, 'content_type', '').lower()
    if content_type and content_type not in allowed_mimes:
        raise ValidationError(f"Invalid MIME type '{content_type}'.")

    # 4. Read header for magic bytes inspection
    initial_pos = uploaded_file.tell() if hasattr(uploaded_file, 'tell') else 0
    header = uploaded_file.read(512)
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(initial_pos)

    # Check for dangerous payload signatures
    for dangerous in DANGEROUS_SIGNATURES:
        if dangerous in header:
            raise ValidationError("File content violates security policy: executable or script signatures detected.")

    # Check that magic signature matches an allowed type
    is_valid_magic = False
    for magic, mime in MAGIC_SIGNATURES.items():
        if header.startswith(magic) and mime in allowed_mimes:
            is_valid_magic = True
            break

    if not is_valid_magic:
        raise ValidationError("File content does not match its claimed file signature.")

    return True
