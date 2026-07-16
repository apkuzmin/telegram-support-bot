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
