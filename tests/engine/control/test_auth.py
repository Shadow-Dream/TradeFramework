"""Control-plane authentication authority tests."""

import secrets
import stat
import tempfile
import unittest
from pathlib import Path

from engine.core import clock as engine_clock
from engine.control import auth as trade_auth


class TradeAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"controlRoot": str(Path(self.temp.name) / "control")}

    def tearDown(self):
        self.temp.cleanup()

    def create_user(self):
        password = secrets.token_urlsafe(32)
        email = f"{secrets.token_hex(8)}@example.invalid"
        user_id = "user-" + secrets.token_hex(12)
        now = engine_clock.utc_now()
        with trade_auth.connect(self.config) as connection:
            connection.execute(
                """
                INSERT INTO users
                (user_id, email, password_hash, role, status, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 'active', ?, ?)
                """,
                (user_id, email, trade_auth.hash_password(password), now, now),
            )
            connection.commit()
        return email, password

    def test_passwords_are_salted_scrypt_digests(self):
        password = secrets.token_urlsafe(32)
        first = trade_auth.hash_password(password)
        second = trade_auth.hash_password(password)

        self.assertTrue(first.startswith("scrypt$"))
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)
        self.assertTrue(trade_auth.verify_password(password, first))
        self.assertFalse(trade_auth.verify_password(password + "x", first))

    def test_session_and_csrf_tokens_are_not_stored_in_plaintext(self):
        trade_auth.ensure_default_user(self.config)
        email, password = self.create_user()
        result = trade_auth.login(self.config, email, password, "127.0.0.1")
        database_bytes = trade_auth.auth_db_path(self.config).read_bytes()

        self.assertNotIn(result["token"].encode(), database_bytes)
        self.assertNotIn(result["csrfToken"].encode(), database_bytes)
        self.assertIsNotNone(trade_auth.authenticate(
            self.config, f"{trade_auth.SESSION_COOKIE}={result['token']}"
        ))
        self.assertTrue(trade_auth.validate_csrf(
            trade_auth.authenticate(self.config, f"{trade_auth.SESSION_COOKIE}={result['token']}"),
            result["csrfToken"],
        ))

    def test_auth_storage_is_owner_only_and_logout_revokes_session(self):
        trade_auth.ensure_default_user(self.config)
        email, password = self.create_user()
        result = trade_auth.login(self.config, email, password, "127.0.0.1")
        session = trade_auth.authenticate(
            self.config, f"{trade_auth.SESSION_COOKIE}={result['token']}"
        )

        self.assertEqual(stat.S_IMODE(trade_auth.auth_db_path(self.config).parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(trade_auth.auth_db_path(self.config).stat().st_mode), 0o600)
        trade_auth.logout(self.config, session)
        self.assertIsNone(trade_auth.authenticate(
            self.config, f"{trade_auth.SESSION_COOKIE}={result['token']}"
        ))

    def test_secure_cookie_attributes_are_mandatory_in_production(self):
        cookie = trade_auth.session_cookie(secrets.token_urlsafe(32), secure=True)
        csrf = trade_auth.csrf_cookie(secrets.token_urlsafe(32), secure=True)

        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", csrf)
        self.assertIn("SameSite=Strict", csrf)
        self.assertNotIn("HttpOnly", csrf)


if __name__ == "__main__":
    unittest.main()
