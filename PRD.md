---
tags:
  - project
  - prd
  - backup
status: draft
Created: 2026-04-04
modified: 2026-04-04
---

# OpenClaw Backup Manager — PRD

**Project:** openclaw-backup-manager  
**Author:** Kevin + O.C.  
**Date:** 2026-04-04  
**Status:** Draft — Review Required  
**Location:** `/Volumes/RayCue-Drive/Documents/projects/openclaw-backup-manager/`
**Platform:** macOS (primary), cross-platform via install script

---

## 1. Overview

A retention-aware backup script that automates `openclaw backup create` with intelligent pruning. Inspired by MySQL dump rotation strategies: keep frequent recent backups, progressively fewer historical ones.

---

## 2. Goals

- Run `openclaw backup create` daily via LaunchAgent (macOS)
- Implement tiered retention with folder-based organization
- Support local storage (primary) and optional S3 upload
- Leverage `openclaw backup` subcommands (verify, etc.)
- Native macOS notifications for status/failures
- Clean, maintainable Python with clear config
- Cross-platform support via conditional install

---

## 3. Retention Logic

| Tier | Retention | Example (today = Apr 4) |
|------|-----------|------------------------|
| Daily | Last 7 days | Apr 4, 3, 2, 1, Mar 31, 30, 29 |
| Weekly | 1 per week, last 4 weeks | Week of Mar 24, Mar 17, Mar 10, Mar 3 |
| Monthly | 1 per month, indefinite | Feb 1, Jan 1, Dec 1... |

**Folder Structure:**
```
~/.openclaw/backups/
├── daily/
│   ├── openclaw-backup-2026-04-04T15-28-57.490Z.tar.gz  (7 files)
│   └── ...
├── weekly/
│   └── openclaw-backup-2026-03-24T...  (4 files)
├── monthly/
│   └── openclaw-backup-2026-02-01T...  (indefinite)
├── latest -> daily/openclaw-backup-2026-04-04T15-28-57.490Z.tar.gz
└── .metadata/
    └── rotation.log
```

**Rotation Logic:**
1. Create new backup in `daily/`
2. Move oldest daily (8th) to `weekly/` if no backup from that week exists
3. Move oldest weekly (5th) to `monthly/` if no backup from that month exists
4. Delete overflow from `monthly/` based on retention policy

**Definitions:**
- **"Oldest"** = determined by file creation time (ctime), not modification time (mtime) or filename parsing
- **"Week"** = ISO 8601 week (Monday–Sunday)
- **"Week exists" check** = if any backup in `weekly/` has the same ISO week number as the candidate backup

---

## 4. Backup Naming

Use timestamped filenames for easy parsing. OpenClaw generates this format:

```
openclaw-backup-2026-04-04T15-28-57.490Z.tar.gz
openclaw-backup-2026-04-03T14-22-15.123Z.tar.gz
```

**Format:** `openclaw-backup-<ISO8601-with-hyphens>.tar.gz`
- Timestamp uses hyphens (`-`) instead of colons (`:`) for filesystem compatibility
- Includes millisecond precision (`.490Z`)
- UTC timezone indicated by `Z` suffix

---

## 5. CLI Commands — RESEARCHED ✓

### `openclaw backup create [options]`

| Option | Description |
|--------|-------------|
| `--output <path>` | Archive path or destination directory |
| `--verify` | Verify after writing (built-in) |
| `--json` | JSON output for parsing |
| `--dry-run` | Preview without writing |
| `--no-include-workspace` | Exclude workspaces |
| `--only-config` | Config file only |

**JSON Output (key fields):**
```json
{
  "createdAt": "2026-04-04T15:28:57.490Z",
  "archivePath": "/path/to/2026-04-04T15-28-57.490Z-openclaw-backup.tar.gz",
  "dryRun": false,
  "verified": true
}
```

### `openclaw backup verify [options] <archive>`

Validates archive integrity. Supports `--json` for machine-readable output.

### Notes
- No `list`, `restore`, or `delete` subcommands — we manage files directly
- No built-in retention — we implement our own pruning

---

## 6. Script Architecture

```
backup-manager/
├── backup_manager.py    # Core logic
├── config.yaml          # Retention rules, paths, S3 settings
├── requirements.txt     # Dependencies
├── install.sh           # Platform-aware installer
├── com.openclaw.backup.plist  # LaunchAgent (macOS)
├── README.md            # Usage & setup
└── tests/               # Unit tests
    ├── test_retention.py
    └── test_rotation.py
```

### Core Components

1. **Config Loader** — YAML with validation
2. **Backup Runner** — Execute `openclaw backup create`, capture output
3. **Tiered Organizer** — Move backups between daily/weekly/monthly folders
4. **Pruner** — Delete overflow based on retention rules
5. **S3 Uploader** — Optional boto3 integration (sync to S3 after rotation)
6. **Notifier** — macOS notifications via osascript
7. **Verifier** — Run `openclaw backup verify` if enabled

---

## 7. Configuration (Draft)

```yaml
# config.yaml
backup:
  output_dir: "~/.openclaw/backups"
  temp_dir: "~/.openclaw/backups/.temp"
  verify_after: true
  include_workspace: true
  pre_check_disk_space: true
  min_free_gb: 5

retention:
  daily: 7      # Keep 7 backups in daily/
  weekly: 4     # Keep 4 backups in weekly/
  monthly: 12   # Keep 12 months (or -1 for indefinite)

rotation:
  timezone: "America/Los_Angeles"
  week_starts_on: "monday"  # or "sunday"

health_check:
  enabled: true
  max_backup_age_hours: 48

storage:
  local:
    enabled: true
    path: "~/.openclaw/backups"
  s3:
    enabled: false
    bucket: "my-backups"
    prefix: "openclaw-backups/"
    region: "us-east-1"
    profile: "default"  # AWS profile to use (optional)
    sync_mode: "mirror"  # mirror | append-only
    storage_class: "STANDARD"

notifications:
  enabled: true
  on_success: false   # Only notify on failure (or true for all)
  on_failure: true
  platform: "macos"   # auto-detected by install.sh

options:
  dry_run: false
```

---

## 8. Execution Flow

```
0. Pre-flight checks:
   a. Check for existing lock file (prevent concurrent runs)
   b. Check disk space (fail if less than min_free_gb)
   c. Validate config schema
1. Load config
2. Create output_dir structure (daily/, weekly/, monthly/, .metadata/)
3. Run: openclaw backup create --output <daily_dir>
4. Capture backup path from JSON output
5. If verify enabled: openclaw backup verify <path>
6. Rotate backups:
   a. If daily/ > 7 files: move oldest to weekly/
   b. If weekly/ > 4 files: move oldest to monthly/
   c. If monthly/ > retention: delete oldest
7. Update latest symlink → daily/most-recent
8. If S3 enabled: sync local structure to S3 mirror (see S3 Strategy below)
9. Send notification (success/failure)
10. Log summary
```

### S3 Strategy: Mirror Mode (Option A)

When both local and S3 storage are enabled, S3 acts as a **mirror** of local storage:

- **Source of truth:** Local filesystem
- **Sync behavior:** After local rotation completes, sync entire tier structure to S3
  - Upload new backups
  - Delete S3 objects that no longer exist locally
  - Maintain identical folder structure (`daily/`, `weekly/`, `monthly/`)
- **Recovery:** If local machine fails, latest backup is at most 1 day old on S3
- **Restore:** Manual download from S3 or use AWS CLI

**S3 Configuration Notes:**
- Enable **S3 Versioning** for accidental deletion protection (optional but recommended)
- Consider **S3 Lifecycle Policies** for cost optimization:
  - Move old versions to Glacier after 30 days
  - Expire noncurrent versions after 90 days
- Use **SSE-S3** or **SSE-KMS** encryption at rest (enabled by default)
- IAM permissions needed: `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`

**Example IAM Policy:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::my-backups",
                "arn:aws:s3:::my-backups/openclaw-backups/*"
            ]
        }
    ]
}
```

---

## 9. Scheduling

### macOS: LaunchAgent (Preferred)

**File:** `com.openclaw.backup.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" 
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.openclaw.backup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/backup_manager.py</string>
        <string>--config</string>
        <string>/path/to/config.yaml</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>~/Library/Logs/openclaw-backup/openclaw-backup.log</string>
    <key>StandardErrorPath</key>
    <string>~/Library/Logs/openclaw-backup/openclaw-backup.error.log</string>
</dict>
</plist>
```

**Install:**
```bash
install.sh
# Detects platform, installs LaunchAgent (macOS) or cron (Linux)
```

### Linux: Cron (Fallback)

```bash
# Daily at 2 AM
0 2 * * * /usr/bin/python3 /path/to/backup_manager.py --config /path/to/config.yaml
```

---

## 10. Notifications

### macOS Native Notifications

**Method:** `osascript` (built-in, no dependencies)

```python
import subprocess

def notify(title: str, message: str, subtitle: str = ""):
    """Send macOS notification."""
    cmd = [
        "osascript", "-e",
        f'display notification "{message}" with title "{title}"'
    ]
    if subtitle:
        cmd[-1] += f' subtitle "{subtitle}"'
    subprocess.run(cmd, capture_output=True)

# Usage:
notify("OpenClaw Backup", "Backup complete", "7 daily, 4 weekly retained")
notify("OpenClaw Backup", "Backup failed", "Verify step failed")
```

**Future:** pyobjc for native NSUserNotificationCenter (no subprocess)

### Other Platforms

| Platform | Method | Status |
|----------|--------|--------|
| Linux | notify-send / dbus | Future |
| Windows | win10toast / native | Future |

---

## 11. Error Handling & Recovery

### Concurrent Run Prevention (Lock File)

To prevent multiple backup runs from interfering with each other:

1. **Lock file location:** `~/.openclaw/backups/.lock`
2. **On start:** Check if lock file exists and is held by a running process
3. **If locked:** Exit with error code and log "Backup already in progress (PID: X)"
4. **On completion:** Remove lock file (use `finally` block for cleanup)
5. **Stale lock detection:** If lock file exists but process no longer runs, remove and proceed

**Implementation:**
```python
import fcntl
import os

def acquire_lock():
    lock_path = os.path.expanduser("~/.openclaw/backups/.lock")
    try:
        lock_fd = open(lock_path, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        return lock_fd
    except BlockingIOError:
        raise RuntimeError("Another backup process is already running")
```

### Disk Space Pre-Check

Before creating a backup:

1. **Check free space** on the backup volume
2. **Compare against** `backup.min_free_gb` config
3. **If insufficient:** Fail fast with clear error message and notification
4. **Log:** Current free space and required minimum

**Implementation:**
```python
import shutil

def check_disk_space(path: str, min_gb: int):
    stat = shutil.disk_usage(path)
    free_gb = stat.free / (1024**3)
    if free_gb < min_gb:
        raise RuntimeError(f"Insufficient disk space: {free_gb:.1f}GB < {min_gb}GB required")
```

### S3 Upload Retry with Exponential Backoff

For transient S3 failures:

1. **Retry policy:** 3 attempts with exponential backoff
2. **Base delay:** 2 seconds
3. **Backoff:** 2s, 4s, 8s
4. **On failure:** Log error, notify user, leave local backup intact
5. **Retryable errors:** 5xx, timeouts, connection errors
6. **Non-retryable:** 4xx (immediate fail)

**Implementation:**
```python
import time
from botocore.exceptions import ClientError

def upload_with_retry(s3_client, bucket, key, file_path):
    for attempt in range(3):
        try:
            s3_client.upload_file(file_path, bucket, key)
            return
        except ClientError as e:
            if e.response['Error']['Code'].startswith('5') and attempt < 2:
                time.sleep(2 * (2 ** attempt))  # 2s, 4s
            else:
                raise
```

### Config Validation (YAML Schema Check)

On config load:

1. **Schema validation** against expected structure
2. **Type checking:** Ensure numeric values are numbers, booleans are booleans
3. **Path validation:** Expand `~` and check writability
4. **Required fields:** Verify all required keys are present
5. **On error:** Print helpful message with line number if available

**Required fields:**
- `backup.output_dir`
- `retention.daily`, `retention.weekly`
- `notifications.enabled`

---

## 12. Restore Procedure

### From Local Backup

1. **Locate backup:** Check `~/.openclaw/backups/latest/` symlink or browse tier folders
2. **Extract to temp:**
   ```bash
   mkdir -p ~/.openclaw/restore-temp
   tar -xzf ~/.openclaw/backups/daily/openclaw-backup-2026-04-04T15-28-57.490Z.tar.gz -C ~/.openclaw/restore-temp
   ```
3. **Restore files:** Copy desired files from extracted archive to appropriate locations
4. **Clean up:** Remove `~/.openclaw/restore-temp` when done

### From S3 Backup

1. **List available backups:**
   ```bash
   aws s3 ls s3://my-backups/openclaw-backups/daily/
   ```
2. **Download backup:**
   ```bash
   aws s3 cp s3://my-backups/openclaw-backups/daily/openclaw-backup-2026-04-04T15-28-57.490Z.tar.gz .
   ```
3. **Extract and restore:** Same as local (see above)
4. **Verify integrity:** Optional, run `openclaw backup verify` on downloaded archive

### Notes

- Backups are standard tar.gz archives — any tar tool works
- No special `openclaw restore` subcommand exists — manual extraction required
- Consider restoring to a temporary location first to inspect contents

---

## 13. Open Questions — RESOLVED

| # | Question | Resolution |
|---|----------|------------|
| 3 | Compression | OpenClaw handles this (tar.gz output) |
| 4 | S3 Credentials | Support `profile` in config; use boto3 default chain |
| 5 | Notifications | macOS native via osascript; other platforms TBD |
| 6 | Folder Structure | Tiered: `daily/`, `weekly/`, `monthly/` with rotation |

---

## 14. Success Criteria

- [ ] Daily backups run automatically via LaunchAgent
- [ ] Tiered folder structure maintained correctly
- [ ] Rotation logic moves files daily→weekly→monthly
- [ ] Retention policy prunes old backups without manual intervention
- [ ] macOS notifications display on failure (optional on success)
- [ ] Config is human-readable and editable
- [ ] Install script detects platform and configures appropriately
- [ ] Logs are informative but not noisy
- [ ] (Optional) S3 upload syncs tiered structure

---

## 15. Roadmap

### v1.0 (Current) — Python Implementation
- Local backups with tiered retention
- macOS LaunchAgent scheduling
- S3 mirror sync
- macOS notifications

### v2.0 — Go Rewrite (Future)
**Motivation:** Single-binary distribution, no Python runtime dependency

**Benefits:**
- Compile to single static binary for Linux, macOS, Windows
- No dependency management (`pip install`)
- Faster startup, lower memory footprint
- Easier cross-platform distribution
- Better for daemon/long-running processes

**Trade-offs:**
- Development velocity slower than Python
- Less ecosystem richness (fewer 3rd party libs)
- Explicit error handling (more boilerplate)

**Migration Plan:**
1. Keep Python v1 as reference implementation
2. Port core logic to Go (backup, rotation, S3 sync)
3. Add feature parity (config format stays the same)
4. Release Go binary as v2.0
5. Deprecate Python v1 (maintenance mode only)

---

## 16. SWDL Process Notes

- [x] Plan — Requirements defined
- [ ] Review — Awaiting Kevin review
- [ ] Revise — Update based on feedback
- [ ] Tests — Unit tests for retention logic
- [ ] Dev — Implement final version

---

*Ready for Kevin review. Please comment with any changes needed before we proceed to revise + tests + implementation.*
