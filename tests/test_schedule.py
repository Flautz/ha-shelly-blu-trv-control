"""Tests for the pure schedule helpers."""

from datetime import datetime

import pytest

from custom_components.shelly_blu_trv_schedule.schedule import (
    Limits,
    ScheduleError,
    build_timespec,
    next_change,
    normalize_rule,
    parse_timespec,
    plan_changes,
)

ALL = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.mark.parametrize(
    ("timespec", "expected"),
    [
        ("0 0 8 * * SUN,MON,TUE,WED,THU,FRI,SAT", ("08:00", ALL)),
        ("0 30 5 * * MON-FRI", ("05:30", ALL[:5])),
        ("0 5 4 * * *", ("04:05", ALL)),
        ("0 0 22 * * 0,6", ("22:00", ["sat", "sun"])),
        ("0 0 22 * * sat,SUN", ("22:00", ["sat", "sun"])),
        ("0 0 7 * * 1-7", ("07:00", ALL)),
        ("0 0 8 1 * MON", None),
        ("0 */5 8 * * MON", None),
        ("@sunrise * * MON", None),
        ("garbage", None),
        ("0 0 8 * * FRI-MON", None),
    ],
)
def test_parse_timespec(timespec, expected):
    assert parse_timespec(timespec) == expected


def test_build_timespec_matches_app_format():
    assert build_timespec("08:00", ALL) == "0 0 8 * * SUN,MON,TUE,WED,THU,FRI,SAT"
    assert build_timespec("6:05", ["fri", "mon"]) == "0 5 6 * * MON,FRI"
    assert build_timespec("23:59", "weekend") == "0 59 23 * * SUN,SAT"
    assert build_timespec("7:00", "weekdays") == "0 0 7 * * MON,TUE,WED,THU,FRI"
    for bad_time in ("24:00", "7", "x"):
        with pytest.raises(ScheduleError):
            build_timespec(bad_time, ALL)
    with pytest.raises(ScheduleError):
        build_timespec("07:00", [])
    with pytest.raises(ScheduleError):
        build_timespec("07:00", ["funday"])


def test_normalize_rule():
    assert normalize_rule({"time": "06:30", "days": ["mon"], "target_C": 20.55}) == {
        "enable": True,
        "timespec": "0 30 6 * * MON",
        "target_C": 20.6,
    }
    assert normalize_rule(
        {"rule_id": 3, "enable": False, "timespec": "0 0 8 * * *", "pos": 40.4}
    ) == {
        "rule_id": 3,
        "enable": False,
        "timespec": "0 0 8 * * *",
        "pos": 40,
    }
    with pytest.raises(ScheduleError):
        normalize_rule({"time": "06:30", "days": ["mon"]})
    with pytest.raises(ScheduleError):
        normalize_rule({"time": "06:30", "days": ["mon"], "target_C": 20, "pos": 3})
    with pytest.raises(ScheduleError):
        normalize_rule({"time": "06:30", "days": ["mon"], "target_C": 31})
    with pytest.raises(ScheduleError):
        normalize_rule({"time": "06:30", "days": ["mon"], "target_C": 21}, Limits(5, 20))
    with pytest.raises(ScheduleError):
        normalize_rule({"time": "06:30", "target_C": 21})
    with pytest.raises(ScheduleError):
        normalize_rule({"timespec": "bad", "target_C": 21})


def test_plan_changes():
    current = [
        {"rule_id": 0, "enable": True, "target_C": 21.0, "timespec": "0 0 8 * * *"},
        {"rule_id": 1, "enable": True, "target_C": 8.0, "timespec": "0 0 16 * * *"},
        {"rule_id": 2, "enable": True, "target_C": 18.0, "timespec": "0 0 18 * * *"},
    ]
    desired = [
        {"rule_id": 0, "enable": True, "target_C": 21.0, "timespec": "0 0 8 * * *"},
        {"rule_id": 1, "enable": False, "target_C": 8.0, "timespec": "0 0 17 * * *"},
        {"enable": True, "pos": 30, "timespec": "0 0 20 * * *"},
        {"rule_id": 7, "enable": True, "target_C": 10.0, "timespec": "0 0 22 * * *"},
    ]
    ops = plan_changes(current, desired)
    assert [(o.kind, o.rule_id, o.rule) for o in ops] == [
        ("remove", 2, None),
        ("update", 1, {"enable": False, "timespec": "0 0 17 * * *"}),
        ("add", None, {"enable": True, "pos": 30, "timespec": "0 0 20 * * *"}),
        ("add", None, {"enable": True, "target_C": 10.0, "timespec": "0 0 22 * * *"}),
    ]
    assert plan_changes(current, current) == []
    with pytest.raises(ScheduleError):
        plan_changes(current, [current[0], current[0]])
    with pytest.raises(ScheduleError):
        plan_changes([], [{"enable": True, "target_C": 20, "timespec": "0 0 8 * * *"}] * 11)


def test_next_change():
    rules = [
        {"rule_id": 0, "enable": True, "target_C": 21.0, "timespec": "0 0 8 * * MON-FRI"},
        {"rule_id": 1, "enable": True, "target_C": 17.0, "timespec": "0 0 22 * * *"},
        {"rule_id": 2, "enable": False, "target_C": 30.0, "timespec": "0 0 9 * * *"},
        {"rule_id": 3, "enable": True, "target_C": 30.0, "timespec": "0 0 9 1 * *"},
    ]
    # Friday 23:00 -> next is Saturday 22:00 (weekday rule skipped)
    when, rule = next_change(rules, datetime(2026, 10, 16, 23, 0))
    assert (when, rule["rule_id"]) == (datetime(2026, 10, 17, 22, 0), 1)
    # Monday 07:59 -> 08:00 same day
    when, rule = next_change(rules, datetime(2026, 10, 19, 7, 59))
    assert (when, rule["rule_id"]) == (datetime(2026, 10, 19, 8, 0), 0)
    # Exactly at 08:00 -> the 08:00 rule has already fired
    when, _ = next_change(rules, datetime(2026, 10, 19, 8, 0))
    assert when == datetime(2026, 10, 19, 22, 0)
    assert next_change([rules[2]], datetime(2026, 10, 19)) is None
    # Only one weekly rule on Monday 08:00, now Monday 09:00 -> next Monday
    when, _ = next_change(
        [{"enable": True, "target_C": 20, "timespec": "0 0 8 * * MON"}], datetime(2026, 10, 19, 9)
    )
    assert when == datetime(2026, 10, 26, 8, 0)
