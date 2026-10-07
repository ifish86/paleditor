"""Cron evaluation for the maintenance window."""

from __future__ import annotations

from datetime import datetime

import pytest

from paleditor.errors import ConfigError
from paleditor.scheduler import CronSchedule


def test_the_daily_five_am_reset_from_the_proposal():
    cron = CronSchedule("0 5 * * *")
    assert cron.next_after(datetime(2026, 10, 7, 14, 30)) == datetime(2026, 10, 8, 5, 0)
    # Earlier the same day, the window is still ahead.
    assert cron.next_after(datetime(2026, 10, 7, 4, 59)) == datetime(2026, 10, 7, 5, 0)
    # On the minute, the next one is tomorrow, not this instant again.
    assert cron.next_after(datetime(2026, 10, 7, 5, 0)) == datetime(2026, 10, 8, 5, 0)


def test_steps():
    cron = CronSchedule("*/15 * * * *")
    assert cron.next_after(datetime(2026, 10, 7, 14, 31)) == datetime(2026, 10, 7, 14, 45)


def test_lists():
    cron = CronSchedule("0 5,17 * * *")
    assert cron.next_after(datetime(2026, 10, 7, 6, 0)) == datetime(2026, 10, 7, 17, 0)


def test_ranges():
    cron = CronSchedule("0 5-7 * * *")
    assert cron.next_after(datetime(2026, 10, 7, 5, 30)) == datetime(2026, 10, 7, 6, 0)


def test_weekday():
    cron = CronSchedule("0 5 * * 0")  # Sunday
    result = cron.next_after(datetime(2026, 10, 7, 12, 0))  # a Wednesday
    assert result == datetime(2026, 10, 11, 5, 0)
    assert result.weekday() == 6  # Python's Sunday


def test_day_of_month():
    cron = CronSchedule("30 3 1 * *")
    assert cron.next_after(datetime(2026, 10, 7, 12, 0)) == datetime(2026, 11, 1, 3, 30)


def test_crossing_a_year_boundary():
    cron = CronSchedule("0 0 1 1 *")
    assert cron.next_after(datetime(2026, 6, 1, 0, 0)) == datetime(2027, 1, 1, 0, 0)


def test_february_29_only_matches_a_leap_year():
    cron = CronSchedule("0 0 29 2 *")
    assert cron.next_after(datetime(2027, 3, 1, 0, 0)) == datetime(2028, 2, 29, 0, 0)


def test_day_of_month_and_weekday_are_ored_like_standard_cron():
    cron = CronSchedule("0 5 13 * 5")  # the 13th, or any Friday
    assert cron.matches(datetime(2026, 11, 13, 5, 0))  # a Friday the 13th
    assert cron.matches(datetime(2026, 11, 6, 5, 0))   # a Friday
    assert cron.matches(datetime(2026, 10, 13, 5, 0))  # a Tuesday the 13th
    assert not cron.matches(datetime(2026, 11, 10, 5, 0))  # a Tuesday the 10th


@pytest.mark.parametrize(
    "expression",
    ["0 5 * *", "0 5 * * * *", "", "x 5 * * *", "60 5 * * *", "0 24 * * *",
     "0 5 0 * *", "0 5 * 13 *", "0 5 * * 7", "*/0 * * * *", "5-1 * * * *"],
)
def test_rejects_malformed_expressions(expression):
    with pytest.raises(ConfigError):
        CronSchedule(expression)
