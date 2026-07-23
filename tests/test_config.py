import os
from unittest import TestCase
from unittest.mock import patch

from support_bot.config import DEFAULT_START_MESSAGE, load_config


class ConfigTests(TestCase):
    def test_start_message_defaults_to_english(self) -> None:
        env = {
            "BOT_TOKEN": "test-token",
            "OPERATOR_GROUP_ID": "-1001",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        self.assertEqual(config.start_message, DEFAULT_START_MESSAGE)

    def test_start_message_can_be_overridden_locally(self) -> None:
        env = {
            "BOT_TOKEN": "test-token",
            "OPERATOR_GROUP_ID": "-1001",
            "START_MESSAGE": "Configured welcome",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        self.assertEqual(config.start_message, "Configured welcome")

    def test_admin_bridge_is_disabled_by_default(self) -> None:
        env = {
            "BOT_TOKEN": "test-token",
            "OPERATOR_GROUP_ID": "-1001",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        self.assertFalse(config.admin_bridge_enabled)

    def test_admin_bridge_requires_complete_valid_configuration(self) -> None:
        base_env = {
            "BOT_TOKEN": "test-token",
            "OPERATOR_GROUP_ID": "-1001",
        }
        with patch.dict(
            os.environ,
            {**base_env, "ADMIN_BRIDGE_URL": "http://127.0.0.1:8080"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be set together"):
                load_config()

        with patch.dict(
            os.environ,
            {
                **base_env,
                "ADMIN_BRIDGE_URL": "http://127.0.0.1:8080",
                "ADMIN_BRIDGE_TOKEN": "x" * 64,
                "ADMIN_BRIDGE_BOT_INSTANCE_ID": (
                    "00000000-0000-0000-0000-000000000002"
                ),
            },
            clear=True,
        ):
            config = load_config()

        self.assertTrue(config.admin_bridge_enabled)
