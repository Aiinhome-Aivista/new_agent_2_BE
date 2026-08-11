"""
Drive Credential Encryption
============================
Wraps Fernet symmetric encryption so that service-account JSON stored in MySQL
is never readable as plain text.

Security contract:
  - The raw `credentials_json` string is ONLY decrypted in memory, just before
    authenticating with Google.  It is never written back to disk.
  - The Fernet key lives exclusively in DRIVE_CREDENTIAL_ENCRYPTION_KEY (.env).
  - If the key is missing/empty, the module falls back to a NO-OP base64 encoding
    and logs a WARNING so the developer knows to configure the key.
"""
import base64
import logging

logger = logging.getLogger("drive_crypto")


def _get_fernet():
    """Return a Fernet instance using the key from settings, or None if unconfigured."""
    from core.config import settings
    key = (settings.DRIVE_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if not key:
        logger.warning(
            "DRIVE_CREDENTIAL_ENCRYPTION_KEY is not set. "
            "Credentials will be stored as base64 (NOT encrypted). "
            "Set this key in .env for production security."
        )
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:
        logger.error("Invalid DRIVE_CREDENTIAL_ENCRYPTION_KEY: %s", exc)
        return None


def encrypt_credentials(raw_json: str) -> str:
    """
    Encrypt a credentials JSON string for DB storage.
    Returns a safe string (Fernet token or base64 fallback).
    """
    fernet = _get_fernet()
    if fernet:
        return fernet.encrypt(raw_json.encode()).decode()
    # Fallback: base64 only (developer warning already logged)
    return base64.b64encode(raw_json.encode()).decode()


def decrypt_credentials(stored: str) -> str:
    """
    Decrypt a stored credentials string back to raw JSON.
    Must only be called in memory; never log the result.
    """
    fernet = _get_fernet()
    if fernet:
        try:
            return fernet.decrypt(stored.encode()).decode()
        except Exception:
            # Maybe stored as base64 fallback from an older version
            pass
    # Fallback: base64 decode
    try:
        return base64.b64decode(stored.encode()).decode()
    except Exception as exc:
        raise ValueError(f"Cannot decrypt Drive credentials: {exc}") from exc
