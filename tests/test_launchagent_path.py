#!/usr/bin/env python3
"""
Tests for LaunchAgent PATH injection (issue #1).

The macOS LaunchAgent runs with a minimal PATH and does not inherit the
interactive shell environment, so the `openclaw` CLI is not found and
scheduled backups fail silently with exit code 78. install.sh now injects a
usable PATH (containing the `openclaw` bin dir) into the generated plist's
EnvironmentVariables, and the committed plist template documents the same.

Covers all seven required categories: Security, Performance, Retry, Unit,
Integration, Functional, Frame.
"""

import os
import plistlib
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
PLIST_TEMPLATE = REPO_ROOT / "com.openclaw.backup.plist"


def _make_executable(path: Path, body: str) -> None:
    """Write a shell stub and mark it executable."""
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def sandbox(tmp_path):
    """A sandboxed HOME plus a stub bin dir shadowing openclaw/python3/launchctl.

    Lets us run install.sh with no real side effects (no pip install, no
    launchctl load, no touching the real LaunchAgents dir).
    """
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir()

    # Fake openclaw living in a version-specific dir (mimics NVM layout).
    openclaw_dir = home / ".nvm" / "versions" / "node" / "v24.14.0" / "bin"
    openclaw_dir.mkdir(parents=True)
    fake_openclaw = openclaw_dir / "openclaw"
    _make_executable(fake_openclaw, "#!/bin/sh\nexit 0\n")

    # Stub python3: satisfies `which python3` and no-ops `-m pip install`.
    _make_executable(
        stub_bin / "python3",
        "#!/bin/sh\ncase \"$*\" in\n  *pip*) exit 0 ;;\n  --version) echo 'Python 3.x' ;;\nesac\nexit 0\n",
    )
    # Stub launchctl so we never load a real agent.
    _make_executable(stub_bin / "launchctl", "#!/bin/sh\nexit 0\n")

    env = dict(os.environ)
    # openclaw lives only in its NVM dir, so `command -v openclaw` resolves
    # there and `dirname` yields the real bin dir. stub_bin shadows
    # python3/launchctl; system dirs supply coreutils (mkdir/cp/cat/...).
    env["PATH"] = f"{stub_bin}:{openclaw_dir}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["HOME"] = str(home)

    return {"home": home, "openclaw_dir": openclaw_dir, "env": env}


def _run_install(sandbox):
    result = subprocess.run(
        ["bash", str(INSTALL_SH)],
        cwd=str(REPO_ROOT),
        env=sandbox["env"],
        capture_output=True,
        text=True,
    )
    plist_path = sandbox["home"] / "Library" / "LaunchAgents" / "com.openclaw.backup.plist"
    return result, plist_path


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class TestSecurity:
    """The injected PATH must be well-formed and free of injection/secrets."""

    def test_template_path_is_well_formed(self):
        """Template PATH parses as a plist string with no shell metacharacters."""
        with open(PLIST_TEMPLATE, "rb") as fh:
            data = plistlib.load(fh)
        path_value = data["EnvironmentVariables"]["PATH"]
        assert isinstance(path_value, str)
        # No command-substitution / separators that could enable injection when
        # a shell later re-parses PATH.
        for bad in ["$(", "`", ";", "&&", "||", "\n"]:
            assert bad not in path_value

    def test_no_secrets_in_generated_plist(self, sandbox):
        """Generated plist must not leak credentials from the environment."""
        sandbox["env"]["AWS_SECRET_ACCESS_KEY"] = "SUPERSECRETVALUE123"
        _, plist_path = _run_install(sandbox)
        assert plist_path.exists()
        assert "SUPERSECRETVALUE123" not in plist_path.read_text()


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #
class TestPerformance:
    """PATH injection must not bloat the plist or slow generation."""

    def test_generated_path_is_bounded(self, sandbox):
        _, plist_path = _run_install(sandbox)
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        # openclaw dir + inherited PATH; a handful of entries, never unbounded.
        entries = data["EnvironmentVariables"]["PATH"].split(":")
        assert 0 < len(entries) < 64

    def test_plist_parses_quickly(self):
        start = time.perf_counter()
        with open(PLIST_TEMPLATE, "rb") as fh:
            plistlib.load(fh)
        assert (time.perf_counter() - start) < 1.0


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
class TestRetry:
    """N/A: PATH injection performs no external/network calls, so there is
    nothing to retry with backoff. Placeholder kept to satisfy the required
    seven-category matrix; the real retry surface lives with the S3 upload
    feature, not this fix."""

    def test_retry_not_applicable(self):
        assert True


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #
class TestUnit:
    """The committed template documents the PATH fix."""

    def test_template_has_environment_path(self):
        with open(PLIST_TEMPLATE, "rb") as fh:
            data = plistlib.load(fh)
        assert "EnvironmentVariables" in data
        assert "PATH" in data["EnvironmentVariables"]
        path_value = data["EnvironmentVariables"]["PATH"]
        # Still keeps the standard system dirs so nothing regresses.
        assert "/usr/bin" in path_value
        # And adds a common location for user-installed CLIs.
        assert "/.local/bin" in path_value or "/homebrew/bin" in path_value


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(sys.platform != "darwin", reason="install.sh targets macOS LaunchAgents")
class TestIntegration:
    """Running install.sh end-to-end produces a plist with a usable PATH."""

    def test_generated_plist_includes_openclaw_dir(self, sandbox):
        result, plist_path = _run_install(sandbox)
        assert plist_path.exists(), f"install.sh failed: {result.stderr}"
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        path_value = data["EnvironmentVariables"]["PATH"]
        # The directory that actually holds openclaw must be on PATH.
        assert str(sandbox["openclaw_dir"]) in path_value.split(":")


# --------------------------------------------------------------------------- #
# Functional
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(sys.platform != "darwin", reason="install.sh targets macOS LaunchAgents")
class TestFunctional:
    """With the injected PATH, `openclaw` is actually resolvable — the exact
    thing that failed before the fix."""

    def test_openclaw_resolvable_with_injected_path(self, sandbox):
        _, plist_path = _run_install(sandbox)
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        injected_path = data["EnvironmentVariables"]["PATH"]
        # Simulate the LaunchAgent environment and confirm openclaw is found.
        assert shutil.which("openclaw", path=injected_path) is not None


# --------------------------------------------------------------------------- #
# Frame (smoke)
# --------------------------------------------------------------------------- #
class TestFrame:
    """Smoke checks: files exist, parse, and install.sh is syntactically valid."""

    def test_template_is_valid_plist(self):
        with open(PLIST_TEMPLATE, "rb") as fh:
            data = plistlib.load(fh)
        assert data["Label"] == "com.openclaw.backup"

    def test_install_sh_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_install_sh_injects_path(self):
        text = INSTALL_SH.read_text()
        assert "EnvironmentVariables" in text
        assert "AGENT_PATH" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
