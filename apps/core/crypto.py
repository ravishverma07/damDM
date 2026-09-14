"""
Symmetric encryption for Instagram access tokens.

Tokens are encrypted with a key that is deliberately separate from
SECRET_KEY, so rotating Django's secret key does not invalidate stored
tokens and a leaked SECRET_KEY alone cannot decrypt them.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

__all__ = ["encrypt", "decrypt", "TokenDecryptionError", "generate_key"]


class TokenDecryptionError(Exception):
    """Raised when stored ciphertext cannot be decrypted."""


def generate_key() -> str:
    """Generate a fresh Fernet key (used by the `generate_keys` command)."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    raw = getattr(settings, "TOKEN_ENCRYPTION_KEY", "") or ""
    if not raw:
        raise ImproperlyConfigured(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    key = raw.encode()
    try:
        Fernet(key)
    except (ValueError, TypeError):
        # Accept any passphrase by deriving a valid 32-byte urlsafe key from it.
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return urlsafe base64 ciphertext."""
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt ciphertext produced by :func:`encrypt`."""
    if not ciphertext:
        raise TokenDecryptionError("No ciphertext supplied")
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise TokenDecryptionError("Stored token could not be decrypted") from exc
