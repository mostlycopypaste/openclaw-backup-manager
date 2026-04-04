#!/usr/bin/env bash
# OpenClaw Backup Manager Installation Script

set -euo pipefail

INSTALL_DIR="${HOME}/.openclaw/backup-manager"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_NAME="com.openclaw.backup.plist"

echo "=== OpenClaw Backup Manager Installer ==="

# Check for openclaw CLI
if ! command -v openclaw &> /dev/null; then
    echo "Error: openclaw CLI not found in PATH"
    echo "Please install openclaw first: https://github.com/openclaw/openclaw"
    exit 1
fi

echo "✓ Found openclaw CLI at $(which openclaw)"

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

echo "✓ Found python3 at $(which python3)"

# Create installation directory
mkdir -p "${INSTALL_DIR}"
echo "✓ Created installation directory: ${INSTALL_DIR}"

# Copy files
cp backup_manager.py "${INSTALL_DIR}/"
cp config.yaml "${INSTALL_DIR}/"
cp requirements.txt "${INSTALL_DIR}/"

echo "✓ Copied backup manager files"

# Install Python dependencies
if command -v uv &> /dev/null; then
    echo "Using uv for package management..."
    uv pip install --system -r requirements.txt
else
    echo "Installing with pip..."
    python3 -m pip install --user -r requirements.txt
fi

echo "✓ Installed Python dependencies"

# Make backup_manager.py executable
chmod +x "${INSTALL_DIR}/backup_manager.py"

# Install LaunchAgent
mkdir -p "${LAUNCH_AGENTS_DIR}"

# Generate plist with absolute paths
PYTHON3_PATH=$(which python3)
cat > "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.openclaw.backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON3_PATH}</string>
        <string>${INSTALL_DIR}/backup_manager.py</string>
        <string>--config</string>
        <string>${INSTALL_DIR}/config.yaml</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${HOME}/.openclaw/logs/backup-manager.log</string>

    <key>StandardErrorPath</key>
    <string>${HOME}/.openclaw/logs/backup-manager.error.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

echo "✓ Installed LaunchAgent: ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo "  Scheduled to run daily at 2:00 AM"

# Create log directory
mkdir -p "${HOME}/.openclaw/logs"

# Load the LaunchAgent
if launchctl list | grep -q "com.openclaw.backup"; then
    echo "Unloading existing LaunchAgent..."
    launchctl unload "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}" 2>/dev/null || true
fi

launchctl load "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo "✓ Loaded LaunchAgent"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Configuration file: ${INSTALL_DIR}/config.yaml"
echo "Edit this file to customize backup settings."
echo ""
echo "Manual commands:"
echo "  Run backup now:  ${INSTALL_DIR}/backup_manager.py --config ${INSTALL_DIR}/config.yaml"
echo "  Dry run:         ${INSTALL_DIR}/backup_manager.py --config ${INSTALL_DIR}/config.yaml --dry-run"
echo "  Check schedule:  launchctl list | grep openclaw"
echo "  View logs:       tail -f ${HOME}/.openclaw/logs/backup-manager.log"
echo ""
echo "Scheduled backups will run daily at 2:00 AM."
echo "To change the schedule, edit: ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
