#!/usr/bin/env python3
"""Control-plane account, password, and browser-session authority."""

import base64
import hashlib
import hmac
import secrets
import sqlite3
import sys
import time
from getpass import getpass
from http.cookies import SimpleCookie
from pathlib import Path

from engine.core import clock as engine_clock

SESSION_COOKIE = "trade_session"
CSRF_COOKIE = "trade_csrf"
SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_LOGIN_FAILURES = 5
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32
SCRYPT_MAX_MEMORY = 64 * 1024 * 1024
DEFAULT_EMAIL = "0shadow0dream0@gmail.com"
# This is an irreversible scrypt digest. The bootstrap password is never stored.
DEFAULT_PASSWORD_HASH = "scrypt$32768$8$1$fUmDQGSXkO63M8iG0Fn-Zg$zAQN0kTNia2wAz_y6bXRlkG2_LQXZMiAaR7K6vI2FyY"


__all__ = (
    "CSRF_COOKIE",
    "DEFAULT_EMAIL",
    "DEFAULT_PASSWORD_HASH",
    "SESSION_COOKIE",
    "auth_db_path",
    "authenticate",
    "change_password",
    "connect",
    "cookie_value",
    "csrf_cookie",
    "ensure_default_user",
    "expired_csrf_cookie",
    "expired_session_cookie",
    "hash_password",
    "login",
    "logout",
    "normalize_email",
    "opaque_token_hash",
    "session_cookie",
    "validate_csrf",
    "verify_password",
)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def auth_db_path(config):
    return Path(config["controlRoot"]) / "auth" / "auth.db"


def connect(config):
    path = auth_db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    connection = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            csrf_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, expires_at);
        CREATE TABLE IF NOT EXISTS login_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier_hash TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            attempted_at INTEGER NOT NULL,
            succeeded INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS login_attempts_window
            ON login_attempts(identifier_hash, ip_hash, attempted_at);
        """
    )
    connection.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return connection


def normalize_email(value):
    return str(value or "").strip().casefold()


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value):
    return base64.urlsafe_b64decode(str(value) + "=" * (-len(str(value)) % 4))


def hash_password(password, *, salt=None):
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAX_MEMORY,
        dklen=SCRYPT_LENGTH,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt, expected = str(encoded).split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            str(password).encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAX_MEMORY,
            dklen=len(_b64decode(expected)),
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (TypeError, ValueError, MemoryError):
        return False


def opaque_token_hash(value):
    """Return the stable one-way digest used for session and CSRF tokens."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def ensure_default_user(config):
    if DEFAULT_PASSWORD_HASH == "__GENERATE_BEFORE_USE__":
        raise RuntimeError("The bootstrap account password digest has not been generated.")
    email = normalize_email(DEFAULT_EMAIL)
    now = engine_clock.utc_now()
    with connect(config) as connection:
        row = connection.execute("SELECT user_id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row["user_id"]
        user_id = "user-" + secrets.token_hex(16)
        connection.execute(
            """
            INSERT INTO users
            (user_id, email, password_hash, role, status, created_at, updated_at)
            VALUES (?, ?, ?, 'admin', 'active', ?, ?)
            """,
            (user_id, email, DEFAULT_PASSWORD_HASH, now, now),
        )
        connection.commit()
    return user_id


def _public_user(row):
    return {
        "userId": row["user_id"],
        "email": row["email"],
        "role": row["role"],
        "status": row["status"],
    }


def _purge_expired(connection, now):
    connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    connection.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 24 * 60 * 60,))


def _rate_limited(connection, identifier_hash, ip_hash, now):
    row = connection.execute(
        """
        SELECT COUNT(*) AS failures FROM login_attempts
        WHERE succeeded = 0 AND attempted_at >= ?
          AND (identifier_hash = ? OR ip_hash = ?)
        """,
        (now - LOGIN_WINDOW_SECONDS, identifier_hash, ip_hash),
    ).fetchone()
    return int(row["failures"]) >= MAX_LOGIN_FAILURES


def login(config, email, password, client_ip=""):
    ensure_default_user(config)
    email = normalize_email(email)
    if not email or len(email) > 320 or not isinstance(password, str) or not password or len(password) > 1024:
        raise ValueError("Invalid email or password.")
    identifier_hash = opaque_token_hash(email)
    ip_hash = opaque_token_hash(client_ip)
    now = int(time.time())
    with connect(config) as connection:
        _purge_expired(connection, now)
        if _rate_limited(connection, identifier_hash, ip_hash, now):
            connection.commit()
            raise PermissionError("Too many login attempts. Try again later.")
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        encoded = row["password_hash"] if row else DEFAULT_PASSWORD_HASH
        valid = verify_password(password, encoded)
        succeeded = bool(valid and row and row["status"] == "active")
        connection.execute(
            """
            INSERT INTO login_attempts (identifier_hash, ip_hash, attempted_at, succeeded)
            VALUES (?, ?, ?, ?)
            """,
            (identifier_hash, ip_hash, now, int(succeeded)),
        )
        if not succeeded:
            connection.commit()
            raise ValueError("Invalid email or password.")
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + SESSION_LIFETIME_SECONDS
        connection.execute(
            """
            INSERT INTO sessions
            (token_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                opaque_token_hash(token),
                row["user_id"],
                opaque_token_hash(csrf_token),
                now,
                expires_at,
                now,
            ),
        )
        connection.commit()
        return {
            "token": token,
            "csrfToken": csrf_token,
            "expiresAt": expires_at,
            "user": _public_user(row),
        }


def cookie_value(cookie_header, name):
    if not cookie_header:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return ""
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def authenticate(config, cookie_header):
    token = cookie_value(cookie_header, SESSION_COOKIE)
    if not token:
        return None
    now = int(time.time())
    with connect(config) as connection:
        _purge_expired(connection, now)
        row = connection.execute(
            """
            SELECT s.token_hash, s.csrf_hash, s.expires_at,
                   u.user_id, u.email, u.role, u.status
            FROM sessions s JOIN users u ON u.user_id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'
            """,
            (opaque_token_hash(token), now),
        ).fetchone()
        if not row:
            connection.commit()
            return None
        connection.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now, row["token_hash"]),
        )
        connection.commit()
        return {
            "tokenHash": row["token_hash"],
            "csrfHash": row["csrf_hash"],
            "expiresAt": row["expires_at"],
            "user": _public_user(row),
        }


def validate_csrf(session, csrf_token):
    if not session or not csrf_token:
        return False
    return hmac.compare_digest(
        session["csrfHash"], opaque_token_hash(csrf_token)
    )


def logout(config, session):
    if not session:
        return
    with connect(config) as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (session["tokenHash"],))
        connection.commit()


def change_password(config, session, current_password, new_password):
    if not session:
        raise PermissionError("Authentication required.")
    if not isinstance(new_password, str) or len(new_password) < 12 or len(new_password) > 1024:
        raise ValueError("New password must contain at least 12 characters.")
    user_id = session["user"]["userId"]
    with connect(config) as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        encoded = hash_password(new_password)
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
            (encoded, engine_clock.utc_now(), user_id),
        )
        connection.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, session["tokenHash"]),
        )
        connection.commit()


def session_cookie(token, *, secure=True):
    attributes = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        f"Max-Age={SESSION_LIFETIME_SECONDS}",
        "HttpOnly",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def csrf_cookie(token, *, secure=True):
    attributes = [
        f"{CSRF_COOKIE}={token}",
        "Path=/",
        f"Max-Age={SESSION_LIFETIME_SECONDS}",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def expired_session_cookie(*, secure=True):
    attributes = [
        f"{SESSION_COOKIE}=",
        "Path=/",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "HttpOnly",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def expired_csrf_cookie(*, secure=True):
    attributes = [
        f"{CSRF_COOKIE}=",
        "Path=/",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def _main():
    if len(sys.argv) != 2 or sys.argv[1] != "hash-password":
        raise SystemExit("usage: python3 -m engine.control.auth hash-password")
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    _main()
