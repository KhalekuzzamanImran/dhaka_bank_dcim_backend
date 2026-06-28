import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from decouple import config

logger = logging.getLogger(__name__)
_PREFIX = "fernet:"


def _derive_key(raw_key: str) -> bytes:
    """Return a Fernet-compatible key from any non-empty secret string."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet() -> Fernet:
    secret = config("FIELD_ENCRYPTION_KEY", default=None) or settings.SECRET_KEY
    return Fernet(_derive_key(secret))


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return value
    if str(value).startswith(_PREFIX):
        return str(value)
    token = get_fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Decrypt values created by encrypt_secret.

    Backward compatibility: if a legacy plain-text value is present, return it as-is.
    This allows old seed/demo data to continue working while new production secrets are encrypted.
    """
    if value in (None, ""):
        return value
    value = str(value)
    if not value.startswith(_PREFIX):
        return value
    token = value[len(_PREFIX):]
    try:
        return get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("Failed to decrypt an encrypted field. Check FIELD_ENCRYPTION_KEY.")
        return None
