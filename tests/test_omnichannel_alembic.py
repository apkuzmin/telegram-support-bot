from pathlib import Path
from unittest import TestCase


class OmnichannelAlembicTests(TestCase):
    def test_initial_revision_is_a_fixed_schema_snapshot(self) -> None:
        revision = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "0001_omnichannel_headless.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Base.metadata", revision)
        self.assertIn('op.create_table(\n        "support_messages"', revision)
        self.assertIn(
            "CREATE TRIGGER support_realtime_event_notify",
            revision,
        )
        self.assertIn('op.drop_table("support_messages")', revision)
