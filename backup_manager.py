#!/usr/bin/env python3
"""
OpenClaw Backup Manager

Automated backup with tiered retention:
- Keep last 7 daily backups
- Keep 1 per week for past 4 weeks
- Keep 1 per month indefinitely
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


class OpenClawBackup:
    """Manages OpenClaw backup creation and retention."""

    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config["backup"]["output_dir"]).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
            "openclaw", "backup", "create",
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
            "openclaw", "backup", "verify",
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

    def apply_rotation(self):
        """
        Apply tiered rotation policy with directory-based tiers.

        Rotation logic:
        1. New backup created in daily/
        2. If daily/ has > 7 files, move oldest to weekly/
        3. If weekly/ has > 4 files, move oldest to monthly/
        4. Apply monthly retention (delete excess if configured)
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

        # Rotate daily → weekly if over limit
        while len(daily_backups) > daily_limit:
            oldest = daily_backups.pop(0)
            self.move_backup(oldest, self.weekly_dir)

        # Re-scan weekly after daily rotation
        weekly_backups = self.list_backups_in_dir(self.weekly_dir)

        # Rotate weekly → monthly if over limit
        while len(weekly_backups) > weekly_limit:
            oldest = weekly_backups.pop(0)
            self.move_backup(oldest, self.monthly_dir)

        # Re-scan monthly after weekly rotation
        monthly_backups = self.list_backups_in_dir(self.monthly_dir)

        # Apply monthly retention if configured
        if monthly_limit != -1:
            while len(monthly_backups) > monthly_limit:
                oldest = monthly_backups.pop(0)
                self.delete_backup(oldest)

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

    def run(self):
        """Execute full backup workflow with tiered rotation."""
        self.logger.info("=== OpenClaw Backup Manager ===")

        # Step 1: Create new backup (placed in daily/)
        new_backup_path = self.create_backup()

        if not new_backup_path:
            self.logger.error("Backup creation failed, aborting")
            return 1

        # Step 2: Apply tiered rotation
        self.apply_rotation()

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
    return manager.run()


if __name__ == "__main__":
    sys.exit(main())
