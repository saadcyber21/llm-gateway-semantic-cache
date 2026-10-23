import hashlib
import hmac
import secrets


def issue_key() -> tuple[str, str, str]:
    raw = "gw_" + secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + raw).encode()).hexdigest()
    return raw, salt, digest


def verify_key(raw: str, salt: str, expected_hash: str) -> bool:
    candidate = hashlib.sha256((salt + raw).encode()).hexdigest()
    return hmac.compare_digest(candidate, expected_hash)
