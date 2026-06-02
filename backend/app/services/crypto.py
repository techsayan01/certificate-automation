"""
At-rest encryption for sensitive fields stored in Mongo.

We use Fernet (AES-128-CBC + HMAC) because:
  • Stdlib-style symmetric crypto (one key, no key rotation handshake)
  • Built-in expiry support (we ignore it — we want refresh tokens to outlast keys)
  • Output is URL-safe base64, fine to drop straight into a Mongo string field

Key management:
  • The key lives in Secret Manager in production, env var in dev.
  • Generate one with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  • Rotating the key requires re-encrypting all fields. Out of scope for v1 —
    keep the key safe.

What gets encrypted:
  • Gmail client_secret
  • Gmail refresh_token
  • Canva client_secret + refresh_token (when per-festival Canva is used)
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from backend.app.settings import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().ENCRYPTION_KEY
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"\n"
            "and put it in the environment."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Empty input returns empty output (so optional fields
    don't bloat the DB with placeholder ciphertext)."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string. Empty input returns empty output.
    Raises ValueError on tampered / wrong-key ciphertext."""
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt — wrong key or corrupted data."
        ) from exc
