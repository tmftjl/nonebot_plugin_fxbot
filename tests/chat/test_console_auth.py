"""控制台账户：初始账户、密码校验与登录/改密路由。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.console import account
from src.plugins.nonebot_plugin_fxbot.console.routes import auth as auth_routes


class AccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="fxbot-console-account-"))
        self._path_patch = patch.object(account, "_path", return_value=self._dir / "account.json")
        self._password_patch = patch.object(account, "_new_password", return_value="InitPass12345")
        self._path_patch.start()
        self._password_patch.start()

    def tearDown(self) -> None:
        self._password_patch.stop()
        self._path_patch.stop()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_first_start_creates_root_with_random_password(self) -> None:
        data = account.ensure_account()
        self.assertEqual(data["username"], "root")
        self.assertTrue(data["must_change_password"])
        self.assertNotIn("InitPass12345", (self._dir / "account.json").read_text(encoding="utf-8"))
        self.assertTrue(account.verify("root", "InitPass12345"))
        self.assertFalse(account.verify("root", "wrong-password"))
        self.assertFalse(account.verify("admin", "InitPass12345"))

    def test_existing_account_is_kept(self) -> None:
        first = account.ensure_account()
        second = account.ensure_account()
        self.assertEqual(first["password_hash"], second["password_hash"])

    def test_change_password_validates_old_and_length(self) -> None:
        account.ensure_account()
        with self.assertRaises(ValueError):
            account.change_password("wrong-password", "NewPass12345")
        with self.assertRaises(ValueError):
            account.change_password("InitPass12345", "short")
        with self.assertRaises(ValueError):
            account.change_password("InitPass12345", "InitPass12345")
        account.change_password("InitPass12345", "NewPass12345")
        self.assertTrue(account.verify("root", "NewPass12345"))
        self.assertFalse(account.verify("root", "InitPass12345"))
        self.assertFalse(account.profile()["must_change_password"])


class LoginRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_rejects_wrong_password(self) -> None:
        with patch.object(auth_routes.account, "verify", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                await auth_routes.login({"username": "root", "password": "bad"})
        self.assertEqual(caught.exception.status_code, 401)

    async def test_login_returns_console_token_and_profile(self) -> None:
        with (
            patch.object(auth_routes.account, "verify", return_value=True),
            patch.object(
                auth_routes.account,
                "profile",
                return_value={
                    "username": "root",
                    "must_change_password": True,
                },
            ),
            patch.object(auth_routes, "get_console_token", return_value="console-token"),
        ):
            result = await auth_routes.login({"username": "root", "password": "InitPass12345"})
        self.assertEqual(result["token"], "console-token")
        self.assertTrue(result["must_change_password"])

    async def test_update_password_rotates_token(self) -> None:
        with (
            patch.object(auth_routes.account, "change_password") as change,
            patch.object(
                auth_routes.account,
                "profile",
                return_value={
                    "username": "root",
                    "must_change_password": False,
                },
            ),
            patch.object(auth_routes, "rotate_console_token", return_value="rotated-token"),
        ):
            result = await auth_routes.update_password({"old_password": "old", "new_password": "NewPass12345"})
        change.assert_called_once_with("old", "NewPass12345")
        self.assertEqual(result["token"], "rotated-token")
        self.assertFalse(result["must_change_password"])

    async def test_update_password_reports_validation_error(self) -> None:
        with patch.object(auth_routes.account, "change_password", side_effect=ValueError("原密码不正确")):
            with self.assertRaises(HTTPException) as caught:
                await auth_routes.update_password({"old_password": "bad", "new_password": "NewPass12345"})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, "原密码不正确")


if __name__ == "__main__":
    unittest.main()
