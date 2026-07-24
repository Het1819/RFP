"""Password hashing and verification utilities.

Uses pwdlib's recommended Argon2id configuration. No custom cryptography.
"""

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()

# Precomputed valid Argon2 hash of a fixed, unusable value. Used to perform
# password verification work on the unknown-user path so that the response
# timing does not reveal whether an email identifies a valid active user.
_DUMMY_HASH = _password_hash.hash("dummy-password-for-constant-time-verification-only")


def hash_password(password: str) -> str:
    """Hash a plaintext password. Never log or persist the plaintext input."""
    return _password_hash.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Verify a plaintext password against a stored hash.

    Fails closed (returns False) if the stored hash is missing, malformed, or
    uses an unsupported algorithm. Always performs a hash verification, using
    a dummy hash when no real stored hash is available, to avoid leaking
    account existence via timing.
    """
    target_hash = stored_hash or _DUMMY_HASH
    try:
        return _password_hash.verify(password, target_hash)
    except Exception:
        return False
