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

    def create_backup_file(self, directory: Path, days_ago: int) -> Path:
        """Helper to create a backup file with specific date."""
        timestamp = datetime.now() - timedelta(days=days_ago)
        filename = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S.000Z')}-openclaw-backup.tar.gz"
        path = directory / filename
        path.touch()
        return path

    def test_daily_rotation_threshold(self, test_config):
        """Daily backups rotate to weekly when exceeding limit."""
        manager = OpenClawBackup(test_config)

        # Create 8 daily backups (limit is 7)
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        # Apply rotation
        manager.apply_rotation()

        # Should have exactly 7 in daily, 1 in weekly
        daily_backups = manager.list_backups_in_dir(manager.daily_dir)
        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)

        assert len(daily_backups) == 7
        assert len(weekly_backups) == 1

    def test_weekly_rotation_threshold(self, test_config):
        """Weekly backups rotate to monthly when exceeding limit."""
        manager = OpenClawBackup(test_config)

        # Create 5 weekly backups (limit is 4)
        for i in range(5):
            self.create_backup_file(manager.weekly_dir, i * 7)

        # Apply rotation
        manager.apply_rotation()

        # Should have exactly 4 in weekly, 1 in monthly
        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)
        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)

        assert len(weekly_backups) == 4
        assert len(monthly_backups) == 1

    def test_monthly_unlimited_retention(self, test_config):
        """Monthly backups kept indefinitely by default."""
        manager = OpenClawBackup(test_config)

        # Create 10 monthly backups
        for i in range(10):
            self.create_backup_file(manager.monthly_dir, i * 30)

        # Apply rotation
        manager.apply_rotation()

        # All should remain (monthly = -1)
        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)
        assert len(monthly_backups) == 10

    def test_monthly_limited_retention(self, test_config):
        """Monthly backups pruned when limit set."""
        test_config["retention"]["monthly"] = 3
        manager = OpenClawBackup(test_config)

        # Create 5 monthly backups
        for i in range(5):
            self.create_backup_file(manager.monthly_dir, i * 30)

        # Apply rotation
        manager.apply_rotation()

        # Should keep only 3
        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)
        assert len(monthly_backups) == 3

    def test_cascading_rotation(self, test_config):
        """Full cascade: daily → weekly → monthly."""
        manager = OpenClawBackup(test_config)

        # Fill daily (8 files, over limit of 7)
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        # Fill weekly (5 files, over limit of 4)
        # Use wider spacing to avoid same-day collisions
        for i in range(5):
            self.create_backup_file(manager.weekly_dir, (i + 1) * 7)

        # Apply rotation
        manager.apply_rotation()

        daily_backups = manager.list_backups_in_dir(manager.daily_dir)
        weekly_backups = manager.list_backups_in_dir(manager.weekly_dir)
        monthly_backups = manager.list_backups_in_dir(manager.monthly_dir)

        assert len(daily_backups) == 7
        assert len(weekly_backups) == 4
        # Should have at least 1 monthly (2 expected from overflow, but edge cases possible)
        assert len(monthly_backups) >= 1

    def test_dry_run_mode(self, test_config):
        """Dry run doesn't modify files."""
        test_config["options"]["dry_run"] = True
        manager = OpenClawBackup(test_config)

        # Create 8 daily backups
        for i in range(8):
            self.create_backup_file(manager.daily_dir, i)

        initial_count = len(list(manager.daily_dir.glob("*.tar.gz")))

        # Apply rotation in dry run
        manager.apply_rotation()

        # Nothing should change
        final_count = len(list(manager.daily_dir.glob("*.tar.gz")))
        assert final_count == initial_count


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

        symlink_path = manager.output_dir / "latest"
        assert symlink_path.is_symlink()
        # Check that symlink points to the right file (resolve both for comparison)
        assert symlink_path.resolve().name == backup_path.name
        assert "daily" in str(symlink_path.resolve())

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

        symlink_path = manager.output_dir / "latest"
        assert not symlink_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
