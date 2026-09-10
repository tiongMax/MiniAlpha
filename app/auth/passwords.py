"""Versioned password hashing based on the standard-library scrypt KDF."""

import base64
import hashlib
import hmac
import secrets

_N = 2**14
_R = 8
_P = 1
_LENGTH = 32


def hash_password(password: str) -> str:
    """Hash a password with a fresh salt and embedded work parameters."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_LENGTH
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
    encoded_hash = base64.urlsafe_b64encode(derived).decode("ascii")
    return f"scrypt${_N}${_R}${_P}${encoded_salt}${encoded_hash}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without leaking comparison timing."""
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.urlsafe_b64decode(raw_hash.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(raw_salt.encode("ascii")),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
