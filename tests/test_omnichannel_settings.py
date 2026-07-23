from unittest import TestCase
from unittest.mock import patch

from support_bot.omnichannel.settings import OmnichannelSettings


class OmnichannelSettingsTests(TestCase):
    def test_production_requires_secret_and_trusted_hosts(self) -> None:
        with patch.dict(
            "os.environ",
            {"SUPPORT_ENV": "production"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "AUTH_SECRET"):
                OmnichannelSettings.from_env()

        with patch.dict(
            "os.environ",
            {
                "SUPPORT_ENV": "production",
                "SUPPORT_AUTH_SECRET": (
                    "0123456789abcdef0123456789abcdef"
                ),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "TRUSTED_HOSTS"):
                OmnichannelSettings.from_env()

    def test_production_docs_are_off_by_default(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SUPPORT_ENV": "production",
                "SUPPORT_AUTH_SECRET": (
                    "0123456789abcdef0123456789abcdef"
                ),
                "SUPPORT_TRUSTED_HOSTS": "support.example.com",
            },
            clear=True,
        ):
            settings = OmnichannelSettings.from_env()
        self.assertFalse(settings.expose_docs)
        self.assertEqual(
            settings.trusted_hosts,
            ("support.example.com",),
        )
