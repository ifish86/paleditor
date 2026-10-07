"""Password hashing and session cookies.

Auth is deliberately small: one shared password for the friend group, one for
the owner, and an owner flag on the destructive endpoints. That is
proportionate to a dozen known users, and the write path is gated by the
maintenance window regardless.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import db
from .config import AuthConfig

SESSION_COOKIE = "paleditor_session"
SESSION_KEY_STATE = "session_signing_key"

ROLE_FRIEND = "friend"
ROLE_OWNER = "owner"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Generate an argon2id hash for the config file."""
    if not password:
        raise ValueError("password cannot be empty")
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def identify(auth: AuthConfig, password: str) -> str | None:
    """Return the role this password unlocks, or None.

    The owner password is checked first so that an owner is never downgraded to
    friend, and both are always checked so the response time does not reveal
    which one matched.
    """
    is_owner = verify_password(auth.owner_password_hash, password)
    is_friend = verify_password(auth.password_hash, password)
    if is_owner:
        return ROLE_OWNER
    if is_friend:
        return ROLE_FRIEND
    return None


# -- session tokens --------------------------------------------------------


def signing_key(conn: sqlite3.Connection) -> bytes:
    """Fetch the session signing key, generating it on first use.

    Generated rather than configured so there is no default secret anywhere in
    the repo. Losing it only logs everyone out.
    """
    existing = db.get_state(conn, SESSION_KEY_STATE)
    if existing:
        return base64.urlsafe_b64decode(existing)
    key = secrets.token_bytes(32)
    db.set_state(conn, SESSION_KEY_STATE, base64.urlsafe_b64encode(key).decode())
    conn.commit()
    return key


@dataclass(frozen=True)
class Session:
    role: str
    expires_at: int

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(key: bytes, role: str, *, days: int) -> str:
    payload = {"role": role, "exp": int(time.time()) + days * 86400}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key, body.encode(), sha256).digest())
    return f"{body}.{signature}"


def read(key: bytes, token: str | None) -> Session | None:
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    expected = hmac.new(key, body.encode(), sha256).digest()
    try:
        provided = _unb64(signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    role = payload.get("role")
    expires_at = payload.get("exp")
    if role not in (ROLE_FRIEND, ROLE_OWNER) or not isinstance(expires_at, int):
        return None
    if expires_at < time.time():
        return None
    return Session(role=role, expires_at=expires_at)
