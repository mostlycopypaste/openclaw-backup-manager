# Changelog

All notable changes to this project will be documented in this file.

## [0.1.3] - 2026-05-17

### Fixed
- **Weekly tier time-based expiry.** Weekly backups older than `weekly_limit` weeks are now aged out automatically regardless of count. Previously, stale entries could sit in weekly indefinitely when daily→weekly promotion was blocked by same-week deletions.
- **Correct month-check for monthly promotion.** Uses set-based lookup against all existing monthly entries instead of comparing only to the newest. Prevents edge cases where a month could be missed.

### Added
- `_promote_or_delete_weekly()` helper for reuse across expiry paths.
- 3 new tests for time-based expiry (25 total).

## [0.1.2] - 2026-04-26

### Fixed
- **Tiered backup rotation now uses ISO week/month boundaries** (#4, #5). Backups promote daily→weekly→monthly based on calendar boundaries instead of a count-based cascade, which was incorrectly demoting weekly/monthly archives back to daily.

### Added
- `--rotate-only` flag for running rotation without creating a new backup.
- 22 unit tests covering rotation logic, promotion, and consolidation.

## [0.1.1] - 2026-04-04

### Fixed
- Backup path conflicts — files with the same name from different directories no longer collide.
- Improved usability and error messages.

## [0.1.0] - 2026-04-04

### Added
- Initial implementation: tiered backup manager with daily/weekly/monthly rotation logic.
- YAML-based configuration.
- Launchd plist for scheduled runs.
- Install script for easy setup.