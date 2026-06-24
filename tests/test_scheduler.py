from __future__ import annotations

import sys
import types
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _install_scheduler_stubs() -> None:
    class FakeSettings:
        reconciliation_interval_minutes = 5
        backup_timezone = "Europe/Berlin"
        backup_hour = 5
        discord_backup_channel_id = 123
        database_url = "sqlite:///./data/stream_tracker.db"
        backup_storage_path = ""

    fake_config = types.ModuleType("app.config")
    fake_config.get_settings = lambda: FakeSettings()

    fake_database = types.ModuleType("app.database.database")
    fake_database.SessionLocal = lambda: None

    fake_reconciliation = types.ModuleType("app.services.reconciliation")
    fake_reconciliation.reconcile_live_states = lambda db: None

    fake_points = types.ModuleType("app.services.points")
    fake_points.award_live_points = lambda db, event_multiplier=1, viewer_counts=None: 0
    fake_points.LIVE_POINTS_INTERVAL_MINUTES = 5
    fake_points.EVENT_MULTIPLIER = 2

    fake_roles = types.ModuleType("app.services.roles")
    fake_roles.sync_leaderboard_roles = lambda db: None

    fake_twitch = types.ModuleType("app.integrations.twitch")
    fake_twitch.twitch_api = types.SimpleNamespace()

    fake_models = types.ModuleType("app.database.models")
    fake_models.Streamer = object

    sys.modules["app.config"] = fake_config
    sys.modules["app.database.database"] = fake_database
    sys.modules["app.services.reconciliation"] = fake_reconciliation
    sys.modules["app.services.points"] = fake_points
    sys.modules["app.services.roles"] = fake_roles
    sys.modules["app.integrations.twitch"] = fake_twitch
    sys.modules["app.database.models"] = fake_models


_install_scheduler_stubs()
from app.services import scheduler  # noqa: E402


class SchedulerTimezoneTests(unittest.TestCase):
    def test_backup_now_converts_to_backup_timezone(self):
        utc_now = datetime(2026, 6, 24, 1, 30, tzinfo=timezone.utc)

        backup_now = scheduler._backup_now(utc_now)

        self.assertEqual(backup_now, utc_now.astimezone(ZoneInfo("Europe/Berlin")))

    def test_seconds_until_hour_uses_backup_timezone(self):
        fixed_now = datetime(2026, 6, 24, 3, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        original_backup_now = scheduler._backup_now
        scheduler._backup_now = lambda utc_now=None: fixed_now
        try:
            self.assertEqual(scheduler._seconds_until_hour(5), 90 * 60)
        finally:
            scheduler._backup_now = original_backup_now

    def test_save_last_backup_date_uses_backup_timezone_date(self):
        fixed_now = datetime(2026, 6, 25, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        original_backup_now = scheduler._backup_now
        scheduler._backup_now = lambda utc_now=None: fixed_now

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / ".last_backup"
                original_last_backup_path = scheduler._last_backup_path
                scheduler._last_backup_path = lambda: str(path)
                try:
                    scheduler._save_last_backup_date()
                finally:
                    scheduler._last_backup_path = original_last_backup_path

                self.assertEqual(path.read_text().strip(), "2026-06-25")
        finally:
            scheduler._backup_now = original_backup_now


if __name__ == "__main__":
    unittest.main()
