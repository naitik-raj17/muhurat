"""Application-level encryption for PII columns (phone, email) plus a keyed search hash."""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().pii_encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def pii_hash(value: str) -> str:
    """Deterministic keyed hash so encrypted values can still be matched (dedupe, lookups).

    Callers must pass a normalised value (E.164 phone, lower-cased email).
    """
    key = get_settings().pii_hash_key.encode()
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
