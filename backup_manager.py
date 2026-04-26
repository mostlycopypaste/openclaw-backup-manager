#!/usr/bin/env python3
"""
OpenClaw Backup Manager

Automated backup with tiered retention:
- Keep last 7 daily backups
- Keep 1 per week for past N weeks (default 4)
- Keep 1 per month indefinitely (or N months)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

# Default configuration
DEFAULT_CONFIG = {
    "backup": {
        "output_dir": "~/backup",
        "verify_after": True,
        "include_workspace": True,
    },
    "retention": {
        "daily": 7,
        "weekly": 4,
        "monthly": -1,  # -1 means keep indefinitely
    },
    "storage": {
        "s3": {
            "enabled": False,
            "bucket": "",
            "prefix": "openclaw-backups/",
            "region": "us-east-1",
        },
    },
    "options": {
        "dry_run": False,
        "keep_latest_symlink": True,
    },
}


@dataclass
class BackupFile:
    """Represents a backup archive file."""
    path: Path
    created_at: datetime

    @classmethod
    def from_path(cls, path: Path) -> Optional["BackupFile"]:
        """Parse backup filename to extract timestamp."""
        # Pattern: 2026-04-04T15-28-57.490Z-openclaw-backup.tar.gz
        pattern = r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})\.(\d+)Z"
        match = re.search(pattern, path.name)
        if not match:
            return None

        try:
            # Reconstruct: 2026-04-04T15:28:57.490Z
            date_part = match.group(1)
            hour = match.group(2)
            minute = match.group(3)
            second = match.group(4)
            microsecond = match.group(5)

            iso_str = f"{date_part}T{hour}:{minute}:{second}.{microsecond}+00:00"
            created_at = datetime.fromisoformat(iso_str)
            return cls(path=path, created_at=created_at)
        except ValueError:
            return None

    @property
    def iso_year(self) -> int:
        """ISO calendar year for this backup's date."""
        return self.created_at.isocalendar()[0]

    @property
    def iso_week(self) -> int:
        """ISO week number for this backup's date."""
        return self.created_at.isocalendar()[1]

    @property
    def year_month(self) -> tuple:
        """(year, month) tuple for this backup's date."""
        return (self.created_at.year, self.created_at.month)


class OpenClawBackup:
    """Manages OpenClaw backup creation and retention."""

    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config["backup"]["output_dir"]).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Get openclaw path from config, default to 'openclaw' in PATH
        self.openclaw_path = config.get("openclaw_path", "openclaw")

        # Create tiered subdirectories
        self.daily_dir = self.output_dir / "daily"
        self.weekly_dir = self.output_dir / "weekly"
        self.monthly_dir = self.output_dir / "monthly"

        for dir_path in [self.daily_dir, self.weekly_dir, self.monthly_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        self.setup_logging()

    def setup_logging(self):
        """Configure logging."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger = logging.getLogger(__name__)

    def create_backup(self) -> Optional[Path]:
        """Run openclaw backup create and move to daily/ directory."""
        self.logger.info("Starting backup creation...")

        cmd = [
            self.openclaw_path, "backup", "create",
            "--output", str(self.output_dir),
            "--json",
        ]

        if self.config["backup"].get("verify_after", False):
            cmd.append("--verify")

        if not self.config["backup"].get("include_workspace", True):
            cmd.append("--no-include-workspace")

        if self.config["options"].get("dry_run", False):
            cmd.append("--dry-run")
            self.logger.info("[DRY RUN] Would execute: %s", " ".join(cmd))
            # Simulate creating a file in daily/
            return None

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )

            output = json.loads(result.stdout)
            archive_path = Path(output["archivePath"])

            self.logger.info("Backup created: %s", archive_path)

            if output.get("verified"):
                self.logger.info("Backup verified successfully")

            # Move to daily/ directory
            daily_path = self.daily_dir / archive_path.name
            archive_path.rename(daily_path)
            self.logger.info("Moved to daily tier: %s", daily_path.name)

            return daily_path

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip()
            self.logger.error("Backup failed: %s", error_msg)

            # Provide helpful guidance for common errors
            if "must not be written inside a source path" in error_msg:
                self.logger.error("")
                self.logger.error("The backup output directory cannot be inside the openclaw source directory.")
                self.logger.error("Current output: %s", self.output_dir)
                self.logger.error("Solution: Set output_dir to a location outside openclaw's directory")
                self.logger.error("Example: ~/backup or /path/to/backups")

            return None
        except json.JSONDecodeError as e:
            self.logger.error("Failed to parse backup output: %s", e)
            return None

    def verify_backup(self, archive_path: Path) -> bool:
        """Verify a backup archive."""
        self.logger.info("Verifying backup: %s", archive_path)

        cmd = [
            self.openclaw_path, "backup", "verify",
            str(archive_path),
            "--json",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = json.loads(result.stdout)
            # Verify command outputs validation result
            self.logger.info("Backup verification passed")
            return True

        except subprocess.CalledProcessError as e:
            self.logger.error("Backup verification failed: %s", e.stderr)
            return False
        except json.JSONDecodeError as e:
            self.logger.error("Failed to parse verify output: %s", e)
            return False

    def list_backups_in_dir(self, directory: Path) -> List[BackupFile]:
        """List all backup files in a specific directory."""
        backups = []

        for path in directory.glob("*openclaw-backup*.tar.gz"):
            backup = BackupFile.from_path(path)
            if backup:
                backups.append(backup)

        # Sort by creation time, oldest first for rotation logic
        backups.sort(key=lambda b: b.created_at)
        return backups

    def move_backup(self, backup: BackupFile, dest_dir: Path) -> bool:
        """Move a backup file to a different tier directory."""
        dry_run = self.config["options"].get("dry_run", False)
        dest_path = dest_dir / backup.path.name

        if dry_run:
            self.logger.info("[DRY RUN] Would move: %s → %s", backup.path, dest_path)
            return True

        try:
            backup.path.rename(dest_path)
            self.logger.info("Moved: %s → %s", backup.path.parent.name, dest_dir.name)
            return True
        except OSError as e:
            self.logger.error("Failed to move %s: %s", backup.path, e)
            return False

    def delete_backup(self, backup: BackupFile) -> bool:
        """Delete a backup file."""
        dry_run = self.config["options"].get("dry_run", False)

        if dry_run:
            self.logger.info("[DRY RUN] Would delete: %s", backup.path)
            return True

        try:
            backup.path.unlink()
            self.logger.info("Deleted: %s", backup.path)
            return True
        except OSError as e:
            self.logger.error("Failed to delete %s: %s", backup.path, e)
            return False

    @staticmethod
    def _is_different_week(backup: BackupFile, reference: BackupFile) -> bool:
        """Check if backup is from a different ISO week than reference.

        Compares both ISO year and week number to handle year boundaries correctly.
        """
        return (backup.iso_year, backup.iso_week) != (reference.iso_year, reference.iso_week)

    @staticmethod
    def _is_different_month(backup: BackupFile, reference: BackupFile) -> bool:
        """Check if backup is from a different calendar month than reference.

        Compares both year and month to handle year boundaries correctly.
        """
        return backup.year_month != reference.year_month

    def _consolidate_tier(self, backups: List[BackupFile], key_fn, dest_dir: Path = None) -> List[BackupFile]:
        """Remove duplicates within a tier, keeping the newest per key.

        For each group of backups sharing the same key (ISO week or month),
        keep only the most recent backup. Older duplicates are either
        promoted to the next tier (if dest_dir is provided and they represent
        a new key there) or deleted.

        Args:
            backups: List of backups in this tier (sorted oldest-first)
            key_fn: Function to extract the grouping key (e.g., iso_week tuple)
            dest_dir: Destination directory for promoting duplicates (None = delete)

        Returns:
            List of remaining backups (deduplicated) with newest per key.
        """
        if not backups:
            return backups

        # Group by key, keeping track of all backups per group
        groups: Dict[tuple, List[BackupFile]] = {}
        for backup in backups:
            key = key_fn(backup)
            if key not in groups:
                groups[key] = []
            groups[key].append(backup)

        # For each group with more than one backup, keep the newest,
        # delete or promote the rest
        remaining = []
        for key, group_backups in groups.items():
            # group_backups is sorted oldest-first (inherited from input)
            # Keep the newest (last), process the rest
            for backup in group_backups[:-1]:
                self.logger.info(
                    "Consolidating duplicate in %s: %s (keeping newer %s)",
                    backup.path.parent.name, backup.path.name,
                    group_backups[-1].path.name,
                )
                self.delete_backup(backup)
            remaining.append(group_backups[-1])  # keep newest per key

        # Sort remaining by date
        remaining.sort(key=lambda b: b.created_at)
        return remaining

    def apply_rotation(self):
        """
        Apply tiered rotation policy with week/month-aware promotion.

        Rotation logic:
        1. Consolidate weekly tier: keep only 1 backup per ISO week (newest wins).
        2. Consolidate monthly tier: keep only 1 backup per calendar month (newest wins).
        3. Daily rotation: keep the newest N daily backups. Excess backups that
           represent a new ISO week get promoted to weekly; same-week excess
           is deleted.
        4. Weekly rotation: if over the weekly limit, remove oldest. Before
           deleting, check if it represents a different month than the newest
           monthly — if so, promote to monthly.
        5. Monthly retention: keep indefinitely (or N months if configured).
        """
        retention = self.config["retention"]
        daily_limit = retention.get("daily", 7)
        weekly_limit = retention.get("weekly", 4)
        monthly_limit = retention.get("monthly", -1)  # -1 = keep indefinitely

        self.logger.info("Applying rotation: daily=%d, weekly=%d, monthly=%s",
                        daily_limit, weekly_limit,
                        "indefinite" if monthly_limit == -1 else monthly_limit)

        # Get current state of each tier
        daily_backups = self.list_backups_in_dir(self.daily_dir)
        weekly_backups = self.list_backups_in_dir(self.weekly_dir)
        monthly_backups = self.list_backups_in_dir(self.monthly_dir)

        self.logger.info("Current state: daily=%d, weekly=%d, monthly=%d",
                        len(daily_backups), len(weekly_backups), len(monthly_backups))

        # --- Step 0: Consolidate existing tiers ---
        # Fix any pre-existing duplicates within weekly (keep 1 per ISO week)
        # and monthly (keep 1 per calendar month)
        weekly_backups = self._consolidate_tier(
            weekly_backups,
            key_fn=lambda b: (b.iso_year, b.iso_week),
        )
        monthly_backups = self._consolidate_tier(
            monthly_backups,
            key_fn=lambda b: b.year_month,
        )

        # --- Step 1: Daily rotation ---
        # Keep the newest N daily backups. For excess daily backups, try to
        # promote to weekly if they represent a new week; otherwise delete.
        while len(daily_backups) > daily_limit:
            oldest_daily = daily_backups.pop(0)  # oldest (sorted oldest-first)

            # Should we promote this to weekly?
            promote_to_weekly = True
            if weekly_backups:
                # Only promote if this backup is from a different ISO week
                # than the most recent weekly backup
                newest_weekly = weekly_backups[-1]  # newest (sorted oldest-first)
                if not self._is_different_week(oldest_daily, newest_weekly):
                    self.logger.info(
                        "Daily backup %s is same ISO week (%d-%02d) as newest weekly %s — deleting instead of promoting",
                        oldest_daily.path.name,
                        oldest_daily.iso_year, oldest_daily.iso_week,
                        newest_weekly.path.name,
                    )
                    promote_to_weekly = False

            if promote_to_weekly:
                self.logger.info(
                    "Promoting daily → weekly: %s (ISO week %d-%02d)",
                    oldest_daily.path.name, oldest_daily.iso_year, oldest_daily.iso_week,
                )
                self.move_backup(oldest_daily, self.weekly_dir)
                weekly_backups.append(oldest_daily)
                weekly_backups.sort(key=lambda b: b.created_at)
            else:
                self.delete_backup(oldest_daily)

        # --- Step 2: Weekly rotation ---
        # Re-scan weekly after daily promotions (paths may have changed)
        weekly_backups = self.list_backups_in_dir(self.weekly_dir)

        # Keep weekly backups for the last N weeks. When we have more
        # weekly backups than our limit, remove the oldest ones.
        # Before deleting, check if the oldest represents a different month
        # than the newest monthly — if so, promote it instead.
        while len(weekly_backups) > weekly_limit:
            oldest_weekly = weekly_backups.pop(0)  # oldest

            # Should we promote this to monthly?
            promote_to_monthly = True
            if monthly_backups:
                newest_monthly = monthly_backups[-1]  # newest (sorted oldest-first)
                if not self._is_different_month(oldest_weekly, newest_monthly):
                    self.logger.info(
                        "Weekly backup %s is same month (%s) as newest monthly %s — deleting instead of promoting",
                        oldest_weekly.path.name,
                        f"{oldest_weekly.created_at.year}-{oldest_weekly.created_at.month:02d}",
                        newest_monthly.path.name,
                    )
                    promote_to_monthly = False

            if promote_to_monthly:
                self.logger.info(
                    "Promoting weekly → monthly: %s (month %s)",
                    oldest_weekly.path.name,
                    f"{oldest_weekly.created_at.year}-{oldest_weekly.created_at.month:02d}",
                )
                self.move_backup(oldest_weekly, self.monthly_dir)
                monthly_backups.append(oldest_weekly)
                monthly_backups.sort(key=lambda b: b.created_at)
            else:
                self.delete_backup(oldest_weekly)

        # --- Step 3: Monthly retention ---
        # Re-scan monthly after weekly promotions
        monthly_backups = self.list_backups_in_dir(self.monthly_dir)

        if monthly_limit != -1:
            # Keep only the N most recent monthly backups
            while len(monthly_backups) > monthly_limit:
                oldest_monthly = monthly_backups.pop(0)
                self.delete_backup(oldest_monthly)

    def update_latest_symlink(self, latest: Path):
        """Update 'latest.tar.gz' symlink to point to most recent backup."""
        if not self.config["options"].get("keep_latest_symlink", True):
            return

        symlink_path = self.output_dir / "latest.tar.gz"

        try:
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()
            # Create relative symlink: latest.tar.gz → daily/filename.tar.gz
            relative_path = Path("daily") / latest.name
            symlink_path.symlink_to(relative_path)
            self.logger.info("Updated symlink: latest.tar.gz → %s", relative_path)
        except OSError as e:
            self.logger.error("Failed to update symlink: %s", e)

    def run(self, rotate_only: bool = False):
        """Execute full backup workflow with tiered rotation.

        Args:
            rotate_only: If True, skip backup creation and only apply rotation.
        """
        self.logger.info("=== OpenClaw Backup Manager ===")

        if rotate_only:
            self.logger.info("Rotation-only mode (skipping backup creation)")
        else:
            # Step 1: Create new backup (placed in daily/)
            new_backup_path = self.create_backup()

            if not new_backup_path:
                self.logger.error("Backup creation failed, aborting")
                return 1

        # Step 2: Apply tiered rotation
        self.apply_rotation()

        if not rotate_only:
            # Step 3: Update symlink to latest backup
            self.update_latest_symlink(new_backup_path)

        # Step 4: Report final state
        daily_count = len(self.list_backups_in_dir(self.daily_dir))
        weekly_count = len(self.list_backups_in_dir(self.weekly_dir))
        monthly_count = len(self.list_backups_in_dir(self.monthly_dir))

        self.logger.info("Final state: daily=%d, weekly=%d, monthly=%d",
                        daily_count, weekly_count, monthly_count)
        self.logger.info("=== Backup complete ===")
        return 0


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load configuration from file or use defaults."""
    config = DEFAULT_CONFIG.copy()

    # Auto-discover config.yaml in current directory if no path specified
    if config_path is None:
        default_config = Path("config.yaml")
        if default_config.exists():
            config_path = default_config

    if config_path and config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f)
            if user_config:
                # Deep merge would be better, but simple update works for now
                config.update(user_config)

    return config


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw Backup Manager with tiered retention"
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to configuration file"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--rotate-only",
        action="store_true",
        help="Only apply rotation to existing backups (skip backup creation)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Override with CLI args
    if args.dry_run:
        config["options"]["dry_run"] = True

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Run backup manager
    manager = OpenClawBackup(config)
    return manager.run(rotate_only=args.rotate_only)


if __name__ == "__main__":
    sys.exit(main())