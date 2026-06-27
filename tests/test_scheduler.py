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


    def test_perform_backup_local_copy(self):
        # Create temp dir for db and backups
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            db_file = tmpdir_path / "stream_tracker.db"
            db_file.write_text("dummy database content")

            # Mock settings
            original_db_url = scheduler.settings.database_url
            original_storage_path = scheduler.settings.backup_storage_path
            original_discord_id = scheduler.settings.discord_backup_channel_id
            
            scheduler.settings.database_url = f"sqlite:///{db_file}"
            scheduler.settings.backup_storage_path = str(tmpdir_path / "backups")
            scheduler.settings.discord_backup_channel_id = 0 # Disable Discord backup to avoid bot setup

            try:
                # Run backup
                import asyncio
                res = asyncio.run(scheduler._perform_backup())
                self.assertTrue(res)

                # Check if backup file is created
                today_str = scheduler._backup_today().isoformat()
                expected_backup = tmpdir_path / "backups" / f"stream_tracker_{today_str}.db"
                self.assertTrue(expected_backup.exists())
                self.assertEqual(expected_backup.read_text(), "dummy database content")
            finally:
                scheduler.settings.database_url = original_db_url
                scheduler.settings.backup_storage_path = original_storage_path
                scheduler.settings.discord_backup_channel_id = original_discord_id

    def test_perform_backup_rotation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            db_file = tmpdir_path / "stream_tracker.db"
            db_file.write_text("db")

            backups_dir = tmpdir_path / "backups"
            backups_dir.mkdir()

            # Create 9 old backups with different dates
            for i in range(1, 10):
                # e.g., stream_tracker_2026-06-01.db to stream_tracker_2026-06-09.db
                day = f"{i:02d}"
                f = backups_dir / f"stream_tracker_2026-06-{day}.db"
                f.write_text(f"backup_{i}")

            # Mock settings
            original_db_url = scheduler.settings.database_url
            original_storage_path = scheduler.settings.backup_storage_path
            original_discord_id = scheduler.settings.discord_backup_channel_id
            original_backup_now = scheduler._backup_now
            
            scheduler.settings.database_url = f"sqlite:///{db_file}"
            scheduler.settings.backup_storage_path = str(backups_dir)
            scheduler.settings.discord_backup_channel_id = 0
            # Freeze backup time to 2026-06-10
            scheduler._backup_now = lambda utc_now=None: datetime(2026, 6, 10, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))

            try:
                import asyncio
                res = asyncio.run(scheduler._perform_backup())
                self.assertTrue(res)

                # There should be exactly 7 backups kept now:
                # stream_tracker_2026-06-04.db to stream_tracker_2026-06-10.db (total 7)
                files = sorted([f.name for f in backups_dir.glob("stream_tracker_*.db")])
                
                # Check that we have exactly 7 files
                self.assertEqual(len(files), 7)
                
                # The oldest ones (2026-06-01, 2026-06-02, 2026-06-03) should be deleted.
                # So we expect 04, 05, 06, 07, 08, 09, and the new 10.
                expected_files = [
                    "stream_tracker_2026-06-04.db",
                    "stream_tracker_2026-06-05.db",
                    "stream_tracker_2026-06-06.db",
                    "stream_tracker_2026-06-07.db",
                    "stream_tracker_2026-06-08.db",
                    "stream_tracker_2026-06-09.db",
                    "stream_tracker_2026-06-10.db",
                ]
                self.assertEqual(files, expected_files)
            finally:
                scheduler.settings.database_url = original_db_url
                scheduler.settings.backup_storage_path = original_storage_path
                scheduler.settings.discord_backup_channel_id = original_discord_id
                scheduler._backup_now = original_backup_now


if __name__ == "__main__":
    unittest.main()
