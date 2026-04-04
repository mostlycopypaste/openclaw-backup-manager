# OpenClaw Backup Manager

Automated backup with tiered retention for OpenClaw state.

## Quick Start

```bash
# Automated installation (recommended)
chmod +x install.sh
./install.sh

# Manual run with defaults (uses ~/.openclaw/backups)
python3 backup_manager.py

# Dry run — see what would happen
python3 backup_manager.py --dry-run

# Use custom config
python3 backup_manager.py --config /path/to/config.yaml
```

## Retention Strategy

Backups are stored in tiered directories with automatic rotation:

```
~/.openclaw/backups/
├── daily/    (keeps 7 newest)
├── weekly/   (keeps 4 newest)
└── monthly/  (keeps all by default)
```

**Rotation Logic:**
1. New backup created in `daily/`
2. If `daily/` has > 7 files, oldest moves to `weekly/`
3. If `weekly/` has > 4 files, oldest moves to `monthly/`
4. Monthly backups kept indefinitely (configurable)

| Tier | Retention | Description |
|------|-----------|-------------|
| Daily | 7 backups | Most recent daily backups |
| Weekly | 4 backups | Promoted from daily/ |
| Monthly | Unlimited* | Promoted from weekly/ |

\*Monthly retention is configurable via `retention.monthly` in config.yaml (-1 = unlimited)

## Installation

The `install.sh` script:
- Installs to `~/.openclaw/backup-manager/`
- Sets up Python dependencies
- Configures macOS LaunchAgent for daily 2 AM backups
- Creates log directory at `~/.openclaw/logs/`

```bash
chmod +x install.sh
./install.sh
```

## macOS LaunchAgent

After installation, backups run automatically at 2:00 AM daily.

```bash
# Check if running
launchctl list | grep openclaw

# View logs
tail -f ~/.openclaw/logs/backup-manager.log

# Manually trigger now
launchctl start com.openclaw.backup

# Stop scheduled backups
launchctl unload ~/Library/LaunchAgents/com.openclaw.backup.plist
```

## Configuration

See `config.yaml` for all options:

- **backup.output_dir** — Where archives are stored
- **backup.verify_after** — Run verification after creation
- **retention.daily** — Number of daily backups to keep
- **retention.weekly** — Number of weekly backups to keep
- **retention.monthly** — Number of monthly backups (-1 = keep all)
- **storage.s3** — Optional S3 upload (not yet implemented)

## Requirements

- Python 3.8+
- OpenClaw CLI (`openclaw backup create`)
- PyYAML (`pip install pyyaml`)

## Notes

- Backup filenames use OpenClaw's timestamp format: `2026-04-04T15-28-57.490Z-openclaw-backup.tar.gz`
- A `latest` symlink is maintained pointing to the most recent backup
- Verify runs automatically after each backup if enabled
- Dry-run mode shows what would be deleted without removing files
