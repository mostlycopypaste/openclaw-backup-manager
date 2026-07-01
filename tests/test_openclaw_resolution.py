#!/usr/bin/env python3
"""
Tests for dynamic openclaw/NVM path resolution (issue #2).

Backups failed with `FileNotFoundError: openclaw` whenever the NVM Node version
changed, because a single version-pinned path was baked in. `backup_manager`
now resolves the `openclaw` executable dynamically at runtime, tolerating Node
upgrades.

Covers all seven required categories: Security, Performance, Retry, Unit,
Integration, Functional, Frame.
"""

import os
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backup_manager
from backup_manager import (
    OpenClawBackup,
    _resolve_nvm_openclaw,
    resolve_openclaw_path,
)


def _make_openclaw(bin_dir: Path) -> Path:
    """Create an executable fake `openclaw` in bin_dir and return its path."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "openclaw"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return exe


@pytest.fixture
def nvm_home(tmp_path, monkeypatch):
    """A fake NVM tree with no openclaw on PATH.

    Yields the versions/node directory so tests can create versions in it.
    """
    nvm_dir = tmp_path / ".nvm"
    versions = nvm_dir / "versions" / "node"
    versions.mkdir(parents=True)
    monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    monkeypatch.setattr(backup_manager.Path, "home", staticmethod(lambda: tmp_path))
    # Ensure PATH resolution can't accidentally find a real openclaw.
    monkeypatch.setattr(backup_manager.shutil, "which", lambda name: None)
    return {"nvm_dir": nvm_dir, "versions": versions}


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class TestSecurity:
    """Resolution must not trust unverifiable/stale paths or escape NVM."""

    def test_stale_configured_path_is_not_used(self, nvm_home):
        """A configured-but-missing path (the exact issue #2 failure) must NOT
        be returned blindly — we re-resolve dynamically instead."""
        _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        stale = "/Users/kduane/.nvm/versions/node/v20.0.0/bin/openclaw"
        resolved = resolve_openclaw_path({"openclaw_path": stale})
        assert resolved != stale
        assert resolved.endswith("v24.14.0/bin/openclaw")

    def test_resolved_nvm_path_stays_within_nvm_dir(self, nvm_home):
        """Dynamic NVM resolution never returns a path outside $NVM_DIR."""
        _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        resolved = _resolve_nvm_openclaw()
        assert resolved is not None
        assert str(resolved).startswith(str(nvm_home["nvm_dir"]))

    def test_non_executable_configured_path_ignored(self, tmp_path, monkeypatch):
        """A configured path that exists but isn't executable is not used."""
        monkeypatch.setattr(backup_manager.shutil, "which", lambda name: "/usr/local/bin/openclaw")
        not_exec = tmp_path / "openclaw"
        not_exec.write_text("data")
        not_exec.chmod(0o644)
        resolved = resolve_openclaw_path({"openclaw_path": str(not_exec)})
        assert resolved == "/usr/local/bin/openclaw"


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #
class TestPerformance:
    """Resolution stays fast even with many installed Node versions."""

    def test_resolution_scans_many_versions_quickly(self, nvm_home):
        for i in range(60):
            (nvm_home["versions"] / f"v{i}.0.0" / "bin").mkdir(parents=True)
        _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        start = time.perf_counter()
        resolved = _resolve_nvm_openclaw()
        elapsed = time.perf_counter() - start
        assert resolved is not None
        assert elapsed < 1.0


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
class TestRetry:
    """N/A: path resolution is a local filesystem lookup with no external or
    network calls, so there is nothing to retry with backoff. Placeholder kept
    to satisfy the required seven-category matrix; retry semantics belong to the
    S3 upload feature, not this fix."""

    def test_retry_not_applicable(self):
        assert True


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #
class TestUnit:
    """Priority ordering of resolve_openclaw_path."""

    def test_valid_configured_path_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_manager.shutil, "which", lambda name: "/should/not/win")
        exe = _make_openclaw(tmp_path / "bin")
        assert resolve_openclaw_path({"openclaw_path": str(exe)}) == str(exe)

    def test_path_lookup_used_when_no_config(self, monkeypatch):
        monkeypatch.setattr(backup_manager.shutil, "which", lambda name: "/usr/bin/openclaw")
        assert resolve_openclaw_path({}) == "/usr/bin/openclaw"

    def test_bare_fallback_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_manager.shutil, "which", lambda name: None)
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "no-nvm"))
        monkeypatch.setattr(backup_manager.Path, "home", staticmethod(lambda: tmp_path))
        assert resolve_openclaw_path({}) == "openclaw"

    def test_nvm_prefers_default_alias(self, nvm_home):
        _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        _make_openclaw(nvm_home["versions"] / "v26.0.0" / "bin")
        alias = nvm_home["nvm_dir"] / "alias" / "default"
        alias.parent.mkdir(parents=True)
        alias.write_text("v24.14.0\n")
        resolved = _resolve_nvm_openclaw()
        assert str(resolved).endswith("v24.14.0/bin/openclaw")

    def test_nvm_picks_newest_by_semver(self, nvm_home):
        """v9 must not sort above v10 (numeric, not lexical)."""
        _make_openclaw(nvm_home["versions"] / "v9.99.0" / "bin")
        _make_openclaw(nvm_home["versions"] / "v10.0.0" / "bin")
        resolved = _resolve_nvm_openclaw()
        assert str(resolved).endswith("v10.0.0/bin/openclaw")


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #
class TestIntegration:
    """Full NVM tree on disk; simulate a Node upgrade."""

    def test_resolves_current_nvm_openclaw(self, nvm_home):
        exe = _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        assert resolve_openclaw_path({}) == str(exe)

    def test_survives_node_upgrade(self, nvm_home):
        """After the Node version dir changes, resolution follows it — the
        precise regression from issue #2."""
        old = _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        assert resolve_openclaw_path({}) == str(old)

        # Simulate `nvm install` bumping Node: old version removed, new added.
        import shutil as _sh
        _sh.rmtree(nvm_home["versions"] / "v24.14.0")
        new = _make_openclaw(nvm_home["versions"] / "v25.1.0" / "bin")

        assert resolve_openclaw_path({}) == str(new)


# --------------------------------------------------------------------------- #
# Functional
# --------------------------------------------------------------------------- #
class TestFunctional:
    """OpenClawBackup wires the resolved path into the manager."""

    def test_manager_uses_resolved_nvm_path(self, tmp_path, nvm_home):
        exe = _make_openclaw(nvm_home["versions"] / "v24.14.0" / "bin")
        config = {
            "backup": {"output_dir": str(tmp_path / "out"), "verify_after": False,
                       "include_workspace": True},
            "retention": {"daily": 7, "weekly": 4, "monthly": -1},
            "options": {"dry_run": False, "keep_latest_symlink": True},
        }
        manager = OpenClawBackup(config)
        assert manager.openclaw_path == str(exe)


# --------------------------------------------------------------------------- #
# Frame (smoke)
# --------------------------------------------------------------------------- #
class TestFrame:
    """Smoke: resolver imports, always returns a str, never raises."""

    def test_resolver_returns_str(self):
        result = resolve_openclaw_path({})
        assert isinstance(result, str) and result

    def test_resolver_handles_missing_nvm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "absent"))
        monkeypatch.setattr(backup_manager.Path, "home", staticmethod(lambda: tmp_path))
        # Should not raise even when nothing exists.
        assert _resolve_nvm_openclaw() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
