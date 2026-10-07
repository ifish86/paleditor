"""Cron evaluation and the background thread that runs the window.

A five-field cron expression is all the proposal asks for ("0 5 * * *"), so
this is a small evaluator rather than a dependency. It supports ``*``, numbers,
lists, ranges and steps, which covers every schedule a daily reset needs.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from .errors import ConfigError

log = logging.getLogger(__name__)

# minute, hour, day-of-month, month, day-of-week
FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
MAX_LOOKAHEAD_DAYS = 366


def _parse_field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ConfigError(f"empty cron field component in {text!r}")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ConfigError(f"bad cron step in {text!r}")
            step = int(step_text)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise ConfigError(f"bad cron range in {text!r}")
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ConfigError(f"cannot parse cron field {text!r}")
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise ConfigError(f"cron field {text!r} is out of range {low}-{high}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ConfigError(f"cron field {text!r} matches nothing")
    return values


class CronSchedule:
    def __init__(self, expression: str):
        fields = expression.split()
        if len(fields) != 5:
            raise ConfigError(
                f"cron expression {expression!r} must have five fields, "
                f"got {len(fields)}"
            )
        self.expression = expression
        self.minutes, self.hours, self.days, self.months, self.weekdays = (
            _parse_field(text, low, high)
            for text, (low, high) in zip(fields, FIELD_RANGES)
        )
        # Whether day-of-month and day-of-week were both restricted. Standard
        # cron ORs them in that case; this evaluator follows that.
        self._dom_restricted = fields[2].strip() != "*"
        self._dow_restricted = fields[4].strip() != "*"

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        # Python's weekday() is Monday=0; cron uses Sunday=0.
        dow = (moment.weekday() + 1) % 7
        dom_ok = moment.day in self.days
        dow_ok = dow in self.weekdays
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_after(self, moment: datetime) -> datetime:
        """The first matching minute strictly after ``moment``."""
        candidate = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = candidate + timedelta(days=MAX_LOOKAHEAD_DAYS)
        while candidate < limit:
            if self.matches(candidate):
                return candidate
            # Skip a whole day when the date cannot match, rather than stepping
            # a minute at a time through a year.
            if not self._date_could_match(candidate):
                candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            candidate += timedelta(minutes=1)
        raise ConfigError(
            f"cron expression {self.expression!r} has no next run within a year"
        )

    def _date_could_match(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        dow = (moment.weekday() + 1) % 7
        dom_ok = moment.day in self.days
        dow_ok = dow in self.weekdays
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok


class WindowScheduler:
    """A daemon thread that fires the maintenance window on schedule.

    Deliberately simple: it wakes every 30 seconds, checks whether the next due
    time has passed, and calls the worker. The worker takes the lockfile, so a
    manual run that overlaps is refused there rather than here.
    """

    def __init__(self, schedule: str, callback, *, poll_seconds: float = 30.0):
        self.cron = CronSchedule(schedule)
        self.callback = callback
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_run: datetime = self.cron.next_after(datetime.now())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="paleditor-window", daemon=True
        )
        self._thread.start()
        log.info("window scheduler started; next run %s", self.next_run.isoformat())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            now = datetime.now()
            if now < self.next_run:
                continue
            self.next_run = self.cron.next_after(now)
            try:
                self.callback()
            except Exception:
                # A scheduled window that raises must not kill the thread, or
                # the 5am reset silently stops happening.
                log.exception("scheduled maintenance window failed")
