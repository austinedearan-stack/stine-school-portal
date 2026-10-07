"""Encryption at rest and keyed hashing for authentication artifacts.

* ``encrypt``/``decrypt``: MultiFernet over ``settings.MFA_ENCRYPTION_KEYS`` (first key encrypts,
  all keys decrypt, so keys can be rotated). Used for TOTP secrets (audit finding A-9).
* ``keyed_digest``: HMAC-SHA256 with ``settings.PORTAL_HMAC_KEY`` and a purpose label. Used for
  recovery/enrollment/reset codes, device-cookie nonces and identifier hashes: one indexed lookup,
  no per-row password hashing (so no N x Argon2 DoS), useless to an attacker without the key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

BASE32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid transcription errors


@lru_cache(maxsize=4)
def _multi_fernet(keys: tuple[str, ...]) -> MultiFernet:
    if not keys:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEYS is empty.")
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


def _fernet() -> MultiFernet:
    return _multi_fernet(tuple(settings.MFA_ENCRYPTION_KEYS))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # wrong/rotated-away key or tampered value
        raise ValueError("Encrypted value could not be decrypted.") from exc


def keyed_digest(value: str, *, purpose: str) -> str:
    key = settings.PORTAL_HMAC_KEY
    if not key:
        raise ImproperlyConfigured("PORTAL_HMAC_KEY is not set.")
    message = f"{purpose}\x00{value}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def random_code(length: int) -> str:
    """Human-typable random code from a 32-symbol alphabet (5 bits per character)."""
    return "".join(secrets.choice(BASE32_ALPHABET) for _ in range(length))


def normalise_code(value: str) -> str:
    """Upper-case and strip separators/whitespace a user may type around a code."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())
