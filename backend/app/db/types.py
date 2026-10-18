"""Custom column types."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core import crypto


class EncryptedString(TypeDecorator[str]):
    """Fernet-encrypted text column. Plaintext never reaches the database."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return crypto.encrypt(str(value))

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return crypto.decrypt(value)
