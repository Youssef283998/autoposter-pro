"""Service installers: systemd, launchd, Windows Task Scheduler."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from autoposter.utils.logging import get_logger

logger = get_logger("cli.service")

SYSTEMD_UNIT = """\
[Unit]
Description=AutoPoster Pro scheduler
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
ExecStart={python} -m autoposter.cli.main run
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

LAUNCHD_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.autoposter.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>autoposter.cli.main</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{workdir}/autoposter.log</string>
    <key>StandardErrorPath</key>
    <string>{workdir}/autoposter.log</string>
</dict>
</plist>
"""

WINDOWS_TASK_XML = """\
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>-m autoposter.cli.main run</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install_service(*, workdir: Path, python: str | None = None) -> str:
    """Install a background service for the current OS.

    Returns a status message.
    """
    system = platform.system()
    python = python or _find_python()

    if system == "Linux":
        return _install_systemd(workdir=workdir, python=python)
    if system == "Darwin":
        return _install_launchd(workdir=workdir, python=python)
    if system == "Windows":
        return _install_windows(workdir=workdir, python=python)
    return f"Unsupported platform: {system}"


def uninstall_service() -> str:
    """Uninstall the background service."""
    system = platform.system()
    if system == "Linux":
        return _uninstall_systemd()
    if system == "Darwin":
        return _uninstall_launchd()
    if system == "Windows":
        return _uninstall_windows()
    return f"Unsupported platform: {system}"


def service_status() -> str:
    """Check whether the service is installed and running."""
    system = platform.system()
    if system == "Linux":
        return _systemd_status()
    if system == "Darwin":
        return _launchd_status()
    if system == "Windows":
        return _windows_status()
    return f"Unsupported platform: {system}"


def _find_python() -> str:
    """Find the current Python executable."""
    import sys

    return sys.executable or "python3"


def _install_systemd(*, workdir: Path, python: str) -> str:
    user = os.environ.get("USER", "root")
    unit = SYSTEMD_UNIT.format(user=user, workdir=workdir, python=python)
    unit_path = Path("/etc/systemd/system/autoposter.service")
    try:
        unit_path.write_text(unit, encoding="utf-8")
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["systemctl", "enable", "autoposter"], check=True, capture_output=True)
        subprocess.run(["systemctl", "start", "autoposter"], check=True, capture_output=True)
        return f"systemd service installed and started: {unit_path}"
    except (PermissionError, subprocess.CalledProcessError) as exc:
        return f"Could not install systemd service (need root): {exc}"


def _uninstall_systemd() -> str:
    try:
        subprocess.run(["systemctl", "stop", "autoposter"], check=False, capture_output=True)
        subprocess.run(["systemctl", "disable", "autoposter"], check=False, capture_output=True)
        Path("/etc/systemd/system/autoposter.service").unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
        return "systemd service removed"
    except (PermissionError, subprocess.CalledProcessError) as exc:
        return f"Could not remove systemd service: {exc}"


def _systemd_status() -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "autoposter"], capture_output=True, text=True
        )
        return f"systemd: {result.stdout.strip() or 'unknown'}"
    except FileNotFoundError:
        return "systemd: not available"


def _install_launchd(*, workdir: Path, python: str) -> str:
    plist = LAUNCHD_PLIST.format(workdir=workdir, python=python)
    plist_path = Path.home() / "Library/LaunchAgents/com.autoposter.app.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "load", str(plist_path)], check=False, capture_output=True)
    return f"launchd agent installed: {plist_path}"


def _uninstall_launchd() -> str:
    plist_path = Path.home() / "Library/LaunchAgents/com.autoposter.app.plist"
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
    plist_path.unlink(missing_ok=True)
    return "launchd agent removed"


def _launchd_status() -> str:
    plist_path = Path.home() / "Library/LaunchAgents/com.autoposter.app.plist"
    if not plist_path.exists():
        return "launchd: not installed"
    result = subprocess.run(
        ["launchctl", "list", "com.autoposter.app"], capture_output=True, text=True
    )
    return f"launchd: {'running' if result.returncode == 0 else 'not running'}"


def _install_windows(*, workdir: Path, python: str) -> str:
    xml = WINDOWS_TASK_XML.format(workdir=workdir, python=python)
    xml_path = workdir / "autoposter-task.xml"
    xml_path.write_text(xml, encoding="utf-16")
    subprocess.run(
        ["schtasks", "/create", "/tn", "AutoPoster", "/xml", str(xml_path), "/f"],
        check=False,
        capture_output=True,
    )
    return f"Windows task installed: {xml_path}"


def _uninstall_windows() -> str:
    subprocess.run(
        ["schtasks", "/delete", "/tn", "AutoPoster", "/f"],
        check=False,
        capture_output=True,
    )
    return "Windows task removed"


def _windows_status() -> str:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", "AutoPoster"], capture_output=True, text=True
    )
    return f"Windows: {'installed' if result.returncode == 0 else 'not installed'}"
