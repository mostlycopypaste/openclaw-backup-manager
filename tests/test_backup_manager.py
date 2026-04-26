#!/usr/bin/env python3
"""
Tests for OpenClaw Backup Manager
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backup_manager import BackupFile, OpenClawBackup


@pytest.fixture
def temp_backup_dir():
    """Create temporary backup directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_config(temp_backup_dir):
    """Test configuration with temp directory."""
    return {
        "backup": {
            "output_dir": str(temp_backup_dir),
            "verify_after": False,
            "include_workspace": True,
        },
        "retention": {
            "daily": 7,
            "weekly": 4,
            "monthly": -1,
        },
        "storage": {
            "local": {
                "enabled": True,
                "path": str(temp_backup_dir),
            },
        },
        "options": {
            "dry_run": False,
            "keep_latest_symlink": True,
        },
    }


class TestBackupFile:
    """Test BackupFile parsing."""

    def test_parse_valid_filename(self):
        """Parse valid backup filename."""
        path = Path("2026-04-04T15-28-57.490Z-openclaw-backup.tar.gz")
        backup = BackupFile.from_path(path)

        assert backup is not None
        assert backup.path == path
        assert backup.created_at.year == 2026
        assert backup.created_at.month == 4
        assert backup.created_at.day == 4

    def test_parse_invalid_filename(self):
        """Parse invalid filename returns None."""
        path = Path("invalid-backup.tar.gz")
        backup = BackupFile.from_path(path)

        assert backup is None

    def test_parse_without_extension(self):
        """Parse filename without extension still works if pattern matches."""
        path = Path("2026-04-04T15-28-57.490Z-openclaw-backup")
        backup = BackupFile.from_path(path)

        # Pattern matches the timestamp part, regardless of extension
        assert backup is not None
        assert backup.created_at.year == 2026

    def test_iso_week_properties(self):
        """Test ISO week and year_month properties."""
        # April 14, 2026 is ISO week 16
        path = Path("2026-04-14T09-00-11.797Z-openclaw-backup.tar.gz")
        backup = BackupFile.from_path(path)
        assert backup is not None
        assert backup.iso_week == 16
        assert backup.year_month == (2026, 4)

    def test_is_different_week(self):
        """Test week comparison across ISO weeks."""
        # Apr 12 is Sunday (ISO week 15), Apr 13 is Monday (ISO week 16)
        b_week15_sun = BackupFile.from_path(Path("2026-04-12T09-00-11.797Z-openclaw-backup.tar.gz"))
        b_week16_mon = BackupFile.from_path(Path("2026-04-13T09-00-11.797Z-openclaw-backup.tar.gz"))
        b_week17_mon = BackupFile.from_path(Path("2026-04-20T09-00-11.797Z-openclaw-backup.tar.gz"))

        assert b_week15_sun is not None and b_week16_mon is not None and b_week17_mon is not None

        # Apr 12 (week 15) vs Apr 13 (week 16) — different weeks
        assert OpenClawBackup._is_different_week(b_week15_sun, b_week16_mon) is True

        # Apr 13 and Apr 16 are both ISO week 16
        b_week16_thu = BackupFile.from_path(Path("2026-04-16T09-00-11.797Z-openclaw-backup.tar.gz"))
        assert OpenClawBackup._is_different_week(b_week16_mon, b_week16_thu) is False

        # Apr 13 (week 16) vs Apr 20 (week 17)
        assert OpenClawBackup._is_different_week(b_week16_mon, b_week17_mon) is True

    def test_is_different_month(self):
        """Test month comparison."""
        b_apr = BackupFile.from_path(Path("2026-04-20T09-00-11.797Z-openclaw-backup.tar.gz"))
        b_may = BackupFile.from_path(Path("2026-05-01T09-00-11.797Z-openclaw-backup.tar.gz"))
        b_mar = BackupFile.from_path(Path("2026-03-31T09-00-11.797Z-openclaw-backup.tar.gz"))

        assert b_apr is not None and b_may is not None and b_mar is not None

        # Same month (both April)
        b_apr2 = BackupFile.from_path(Path("2026-04-25T09-00-11.797Z-openclaw-backup.tar.gz"))
        assert OpenClawBackup._is_different_month(b_apr, b_apr2) is False

        # Different months
        assert OpenClawBackup._is_different_month(b_apr, b_may) is True
        assert OpenClawBackup._is_different_month(b_apr, b_mar) is True

        # Year boundary
        b_dec = BackupFile.from_path(Path("2025-12-31T09-00-11.797Z-openclaw-backup.tar.gz"))
        b_jan = BackupFile.from_path(Path("2026-01-01T09-00-11.797Z-openclaw-backup.tar.gz"))
        assert OpenClawBackup._is_different_month(b_dec, b_jan) is True


class TestOpenClawBackup:
    """Test OpenClawBackup class."""

    def test_init_creates_directories(self, test_config):
        """Initialization creates tier directories."""
        manager = OpenClawBackup(test_config)

        assert manager.daily_dir.exists()
        assert manager.weekly_dir.exists()
        assert manager.monthly_dir.exists()

    def test_list_backups_in_dir(self, test_config):
        """List backups from specific directory."""
        manager = OpenClawBackup(test_config)

        # Create test backup files
        for i in range(3):
            timestamp = datetime.now() + timedelta(days=i)
            filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
            (manager.daily_dir / filename).touch()

        backups = manager.list_backups_in_dir(manager.daily_dir)

        assert len(backups) == 3
        # Should be sorted by date (oldest first)
        assert backups[0].created_at < backups[-1].created_at

    def test_move_backup(self, test_config):
        """Move backup between directories."""
        manager = OpenClawBackup(test_config)

        # Create test backup
        timestamp = datetime.now()
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        backup_path = manager.daily_dir / filename
        backup_path.touch()

        backup = BackupFile.from_path(backup_path)
        assert backup is not None

        # Move to weekly
        success = manager.move_backup(backup, manager.weekly_dir)

        assert success
        assert not backup_path.exists()
        assert (manager.weekly_dir / filename).exists()

    def test_delete_backup(self, test_config):
        """Delete backup file."""
        manager = OpenClawBackup(test_config)

        # Create test backup
        timestamp = datetime.now()
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        backup_path = manager.daily_dir / filename
        backup_path.touch()

        backup = BackupFile.from_path(backup_path)
        assert backup is not None

        # Delete
        success = manager.delete_backup(backup)

        assert success
        assert not backup_path.exists()


class TestRotationLogic:
    """Test tiered rotation logic."""

    def create_backup_file(self, directory: Path, days_ago: int, hours_ago: int = 0) -> Path:
        """Helper to create a backup file with specific date.

        Args:
            directory: Target directory (daily/weekly/monthly)
            days_ago: How many days ago the backup should be dated
            hours_ago: Additional hours offset
        """
        timestamp = datetime.now() - timedelta(days=days_ago, hours=hours_ago)
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        path = directory / filename
        path.touch()
        return path

    def test_daily_rotation_keeps_limit(self, test_config):
        """Daily backups are pruned to the configured limit."""
        manager = OpenClawBackup(test_config)

        # Create 10 daily backups (limit is 7)
        for i in range(10):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        daily_backups = manager.list_backups_in_dir(manager.daily_dir)
        assert len(daily_backups) == 7

    def test_daily_excess_promotes_to_weekly_only_if_different_week(self, test_config):
        """Excess daily backups are promoted to weekly only if different ISO week."""
        manager = OpenClawBackup(test_config)

        # Create 8 daily backups all from the same week
        # (using days 0-7, which likely span at most 2 ISO weeks)
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        daily_backups = manager.list_backups_in_dir(manager.daily_dir)
        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)

        # Daily should be pruned to 7
        assert len(daily_backups) == 7

        # Weekly should only have backups from distinct ISO weeks
        # The excess daily is from the same week as recent dailies,
        # so it may be deleted instead of promoted
        iso_weeks = set((b.iso_year, b.iso_week) for b in weekly_backups)
        assert len(weekly_backups) == len(iso_weeks), \
            f"Weekly has {len(weekly_backups)} backups but only {len(iso_weeks)} distinct weeks"

    def test_weekly_has_one_per_week(self, test_config):
        """Weekly directory should contain at most one backup per ISO week."""
        manager = OpenClawBackup(test_config)

        # Create daily backups spanning 5 weeks (35+ days)
        for i in range(38):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)

        # Verify each weekly backup is from a distinct ISO week
        iso_weeks = [(b.iso_year, b.iso_week) for b in weekly_backups]
        assert len(iso_weeks) == len(set(iso_weeks)), \
            f"Weekly has duplicates: {iso_weeks}"

        # Should have at most 4 weekly backups (the configured limit)
        assert len(weekly_backups) <= 4

    def test_weekly_excess_promotes_to_monthly_only_if_different_month(self, test_config):
        """Excess weekly backups promote to monthly only if different month."""
        manager = OpenClawBackup(test_config)

        # Create daily backups spanning 6 weeks
        for i in range(45):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)

        # Monthly should only have backups from distinct months
        months = [b.year_month for b in monthly_backups]
        assert len(months) == len(set(months)), \
            f"Monthly has duplicates: {months}"

    def test_monthly_unlimited_retention(self, test_config):
        """Monthly backups kept indefinitely by default."""
        manager = OpenClawBackup(test_config)

        # Create daily backups spanning 3 months (~100 days)
        for i in range(100):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        # Monthly backups should all be from distinct months and kept
        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)
        months = [b.year_month for b in monthly_backups]
        assert len(months) == len(set(months))

    def test_monthly_limited_retention(self, test_config):
        """Monthly backups pruned when limit set."""
        test_config["retention"]["monthly"] = 2
        manager = OpenClawBackup(test_config)

        # Create daily backups spanning 4 months
        for i in range(130):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)
        assert len(monthly_backups) <= 2

    def test_dry_run_mode(self, test_config):
        """Dry run doesn't modify files."""
        test_config["options"]["dry_run"] = True
        manager = OpenClawBackup(test_config)

        # Create 10 daily backups (over the limit of 7)
        for i in range(10):
            self.create_backup_file(manager.daily_dir, i)

        initial_daily = len(list(manager.daily_dir.glob("*.tar.gz")))

        # Apply rotation in dry run
        manager.apply_rotation()

        # Nothing should change
        final_daily = len(list(manager.daily_dir.glob("*.tar.gz")))
        assert final_daily == initial_daily

    def test_same_week_daily_excess_gets_deleted(self, test_config):
        """When excess daily backup is from same ISO week as newest weekly, it's deleted not promoted."""
        manager = OpenClawBackup(test_config)

        # Pre-populate weekly with a backup from this week
        self.create_backup_file(manager.weekly_dir, 1)

        # Create 8 daily backups, also from this week (same ISO week)
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)

        # All weekly backups should be from distinct ISO weeks
        iso_weeks = [(b.iso_year, b.iso_week) for b in weekly_backups]
        assert len(iso_weeks) == len(set(iso_weeks)), \
            f"Weekly has duplicates within same week: {iso_weeks}"

    def test_rotate_only_mode(self, test_config):
        """Rotate-only mode skips backup creation."""
        test_config["options"]["dry_run"] = True  # so create_backup returns None safely
        manager = OpenClawBackup(test_config)

        # Create some backups to rotate
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        # Should not crash in rotate-only mode
        # (We can't fully test without mocking openclaw CLI, but
        # the rotation logic should still run)
        manager.apply_rotation()

        # Verify rotation happened (even in dry_run, logic should evaluate)
        daily_backups = manager.list_backups_in_dir(manager.daily_dir)
        assert len(daily_backups) == 8  # dry_run doesn't actually move files

    def test_empty_directories(self, test_config):
        """Rotation handles empty directories gracefully."""
        manager = OpenClawBackup(test_config)

        # No backups at all — should not crash
        manager.apply_rotation()

        daily = manager.list_backups_in_dir(manager.daily_dir)
        weekly = manager.list_backups_in_dir(manager.weekly_dir)
        monthly = manager.list_backups_in_dir(manager.monthly_dir)

        assert len(daily) == 0
        assert len(weekly) == 0
        assert len(monthly) == 0


class TestSymlinkManagement:
    """Test symlink creation and updates."""

    def test_update_latest_symlink(self, test_config):
        """Create symlink to latest backup."""
        manager = OpenClawBackup(test_config)

        # Create a backup file
        timestamp = datetime.now()
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        backup_path = manager.daily_dir / filename
        backup_path.touch()

        backup = BackupFile.from_path(backup_path)
        assert backup is not None

        # Update symlink
        manager.update_latest_symlink(backup_path)

        symlink_path = manager.output_dir / "latest.tar.gz"
        assert symlink_path.is_symlink()
        # Check that symlink points to the right file
        assert symlink_path.resolve().name == backup_path.name

    def test_symlink_disabled(self, test_config):
        """Symlink not created when disabled."""
        test_config["options"]["keep_latest_symlink"] = False
        manager = OpenClawBackup(test_config)

        timestamp = datetime.now()
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        backup_path = manager.daily_dir / filename
        backup_path.touch()

        backup = BackupFile.from_path(backup_path)
        manager.update_latest_symlink(backup_path)

        symlink_path = manager.output_dir / "latest.tar.gz"
        assert not symlink_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])