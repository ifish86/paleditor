"""Starting, stopping and observing the Palworld systemd unit.

Behind an interface because the maintenance window is the one piece that must
be tested, and a test must not shell out to systemctl.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Protocol

from .errors import MaintenanceError

log = logging.getLogger(__name__)


class ServerControl(Protocol):
    def is_running(self) -> bool: ...
    def main_pid(self) -> int | None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...

    def unit_exists(self) -> bool:
        """Whether the configured unit is actually known to systemd."""


class SystemdServerControl:
    """Drives the unit with systemctl.

    Needs a polkit rule or a sudoers entry allowing start/stop of this one
    unit; see docs/deployment.md.
    """

    def __init__(self, unit: str, *, timeout: float = 30.0):
        self.unit = unit
        self.timeout = timeout

    def _systemctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["systemctl", *args]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError as exc:
            raise MaintenanceError("systemctl is not available on this host") from exc
        except subprocess.TimeoutExpired as exc:
            raise MaintenanceError(f"{' '.join(cmd)} timed out") from exc
        if check and proc.returncode != 0:
            raise MaintenanceError(
                f"{' '.join(cmd)} failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def unit_exists(self) -> bool:
        """Whether systemd knows this unit at all.

        Checked before the window runs. A typo in server_unit otherwise reads
        as "the server is already stopped", and the window would then write the
        save while the real server was still live and holding the world in
        memory.
        """
        proc = self._systemctl(
            "show", "--property=LoadState", "--value", self.unit, check=False
        )
        return proc.stdout.strip() == "loaded"

    def is_running(self) -> bool:
        proc = self._systemctl("is-active", "--quiet", self.unit, check=False)
        return proc.returncode == 0

    def main_pid(self) -> int | None:
        proc = self._systemctl(
            "show", "--property=MainPID", "--value", self.unit, check=False
        )
        value = proc.stdout.strip()
        if not value.isdigit():
            return None
        pid = int(value)
        return pid or None

    def start(self) -> None:
        log.info("starting %s", self.unit)
        self._systemctl("start", self.unit)

    def stop(self) -> None:
        log.info("stopping %s", self.unit)
        self._systemctl("stop", self.unit)


def pid_alive(pid: int) -> bool:
    """True while the process exists.

    Reads /proc rather than sending signal 0, because the game server runs as
    a different user and kill(0) would raise EPERM for a live process.
    """
    import os

    return os.path.exists(f"/proc/{pid}")


def wait_for_exit(control: ServerControl, *, timeout: float, interval: float = 2.0) -> bool:
    """Poll until the unit is inactive and its PID is gone.

    Step 3 of the window. The countdown is not trusted: a running server keeps
    the world in memory and its next save would overwrite every edit.
    """
    deadline = time.monotonic() + timeout
    pid = control.main_pid()
    while time.monotonic() < deadline:
        if not control.is_running() and (pid is None or not pid_alive(pid)):
            return True
        time.sleep(interval)
        if pid is None:
            pid = control.main_pid()
    return False
