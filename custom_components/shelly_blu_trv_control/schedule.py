"""Pure helpers for BLU TRV schedule rules (no Home Assistant imports).

A rule on the TRV looks like::

    {"rule_id": 0, "enable": true, "target_C": 21.0,
     "timespec": "0 0 8 * * SUN,MON,TUE,WED,THU,FRI,SAT"}

``timespec`` is Shelly cron: ``sec min hour day-of-month month day-of-week``.
Rules created by the Shelly app always have the simple weekly form handled
by :func:`parse_timespec`; anything else is kept as an opaque "advanced"
rule that can still be enabled, disabled, removed or edited as raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Order used by the Shelly app when it writes timespecs.
CRON_DAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
# Order used in the HA API / UI.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

MAX_RULES = 10
DEFAULT_MIN_C = 4.0
DEFAULT_MAX_C = 30.0

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$")


class ScheduleError(ValueError):
    """Raised for an invalid rule or schedule."""


def _parse_dow_token(token: str) -> int:
    """Return 0..6 (0 = Sunday) for a cron day-of-week token."""
    token = token.strip().upper()
    if token in CRON_DAYS:
        return CRON_DAYS.index(token)
    if token.isdigit() and 0 <= int(token) <= 7:
        return int(token) % 7
    raise ValueError(token)


def _parse_dow(field: str) -> set[int]:
    if field in ("*", "?"):
        return set(range(7))
    days: set[int] = set()
    for part in field.split(","):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = _parse_dow_token(start_s), _parse_dow_token(end_s)
            if end_s.strip() == "7":
                end = 7
            if end < start:
                raise ValueError(part)
            days.update(d % 7 for d in range(start, end + 1))
        else:
            days.add(_parse_dow_token(part))
    if not days:
        raise ValueError(field)
    return days


def parse_timespec(timespec: str) -> tuple[str, list[str]] | None:
    """Parse a simple weekly timespec into ("HH:MM", ["mon", ...]).

    Returns None for anything that is not "<sec 0> <min> <hour> * * <days>".
    """
    fields = timespec.split()
    if len(fields) != 6:
        return None
    sec, minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*":
        return None
    if not (sec.isdigit() and minute.isdigit() and hour.isdigit()):
        return None
    if int(sec) != 0 or int(minute) > 59 or int(hour) > 23:
        return None
    try:
        cron_days = _parse_dow(dow)
    except ValueError:
        return None
    days = [WEEKDAYS[(d - 1) % 7] for d in sorted(cron_days, key=lambda d: (d - 1) % 7)]
    return f"{int(hour):02d}:{int(minute):02d}", days


def normalize_time(value: str) -> tuple[int, int]:
    """Parse "H:MM" / "HH:MM" / "HH:MM:SS" into (hour, minute)."""
    match = _TIME_RE.match(str(value))
    if not match:
        raise ScheduleError(f"Invalid time: {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ScheduleError(f"Invalid time: {value!r}")
    return hour, minute


def normalize_days(days: Any) -> list[str]:
    """Return weekday keys in Monday-first order; accepts names or "all"."""
    if isinstance(days, str):
        days = [d for d in re.split(r"[\s,]+", days) if d]
    result: set[str] = set()
    for day in days or []:
        key = str(day).strip().lower()
        if key in ("all", "*", "daily"):
            result.update(WEEKDAYS)
        elif key in ("weekdays", "workdays"):
            result.update(WEEKDAYS[:5])
        elif key in ("weekend", "weekends"):
            result.update(WEEKDAYS[5:])
        elif key[:3] in WEEKDAYS:
            result.add(key[:3])
        else:
            raise ScheduleError(f"Invalid day: {day!r}")
    if not result:
        raise ScheduleError("At least one day is required")
    return [d for d in WEEKDAYS if d in result]


def build_timespec(time: str, days: Any) -> str:
    """Build a timespec in the same form the Shelly app writes."""
    hour, minute = normalize_time(time)
    wanted = set(normalize_days(days))
    cron = ",".join(c for c in CRON_DAYS if c[:3].lower() in wanted)
    # The TRV does not accept leading zeros (e.g. "08").
    return f"0 {minute} {hour} * * {cron}"


@dataclass
class Limits:
    """Target temperature limits from the TRV config."""

    min_c: float = DEFAULT_MIN_C
    max_c: float = DEFAULT_MAX_C


def normalize_rule(rule: dict[str, Any], limits: Limits | None = None) -> dict[str, Any]:
    """Validate a user supplied rule and return the device representation.

    Accepts either ``timespec`` or ``time`` + ``days``. Exactly one of
    ``target_C`` or ``pos`` is required. ``rule_id`` is kept if present.
    """
    limits = limits or Limits()
    out: dict[str, Any] = {}
    if rule.get("rule_id") is not None:
        out["rule_id"] = int(rule["rule_id"])
    out["enable"] = bool(rule.get("enable", True))

    if rule.get("time") is not None or rule.get("days") is not None:
        if rule.get("time") is None or rule.get("days") is None:
            raise ScheduleError("Both time and days are required")
        out["timespec"] = build_timespec(rule["time"], rule["days"])
    elif rule.get("timespec"):
        timespec = " ".join(str(rule["timespec"]).split())
        if len(timespec.split()) not in (5, 6) and not timespec.startswith("@"):
            raise ScheduleError(f"Invalid timespec: {timespec!r}")
        out["timespec"] = timespec
    else:
        raise ScheduleError("A rule needs time and days (or a timespec)")

    has_target = rule.get("target_C") is not None
    has_pos = rule.get("pos") is not None
    if has_target == has_pos:
        raise ScheduleError("A rule needs exactly one of target_C or pos")
    if has_target:
        try:
            target = round(float(rule["target_C"]), 1)
        except (TypeError, ValueError) as err:
            raise ScheduleError(f"Invalid temperature: {rule['target_C']!r}") from err
        if not limits.min_c <= target <= limits.max_c:
            raise ScheduleError(f"Temperature {target} °C outside {limits.min_c}–{limits.max_c} °C")
        out["target_C"] = target
    else:
        try:
            pos = int(round(float(rule["pos"])))
        except (TypeError, ValueError) as err:
            raise ScheduleError(f"Invalid valve position: {rule['pos']!r}") from err
        if not 0 <= pos <= 100:
            raise ScheduleError(f"Valve position {pos} % outside 0–100 %")
        out["pos"] = pos
    return out


def describe_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Return a device rule enriched with parsed ``time`` and ``days``."""
    out = {k: rule[k] for k in ("rule_id", "enable", "timespec", "target_C", "pos") if k in rule}
    parsed = parse_timespec(rule.get("timespec", ""))
    out["time"], out["days"] = parsed if parsed else (None, None)
    return out


def sort_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort rules by time of day; unparseable rules go last."""

    def key(rule: dict[str, Any]) -> tuple[int, str, int]:
        parsed = parse_timespec(rule.get("timespec", ""))
        if parsed is None:
            return (1, rule.get("timespec", ""), rule.get("rule_id", 0))
        return (0, parsed[0], rule.get("rule_id", 0))

    return sorted(rules, key=key)


def _payload(rule: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rule.items() if k != "rule_id"}


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 0.05
    return a == b


@dataclass
class Operation:
    """A single RPC needed to turn the current schedule into the desired one."""

    kind: str  # "remove" | "update" | "add"
    rule_id: int | None = None
    rule: dict[str, Any] | None = None


def plan_changes(current: list[dict[str, Any]], desired: list[dict[str, Any]]) -> list[Operation]:
    """Compute the RPC operations to replace ``current`` with ``desired``.

    ``desired`` must already be normalized. Rules carrying a ``rule_id``
    that exists on the device are updated in place (only changed fields are
    sent); others are added; device rules not referenced are removed.
    Removals come first so slots are free for additions.
    """
    if len(desired) > MAX_RULES:
        raise ScheduleError(f"The TRV supports at most {MAX_RULES} rules")
    by_id = {r["rule_id"]: r for r in current}
    seen: set[int] = set()
    updates: list[Operation] = []
    adds: list[Operation] = []
    for rule in desired:
        rule_id = rule.get("rule_id")
        if rule_id is not None and rule_id in seen:
            raise ScheduleError(f"Duplicate rule_id {rule_id}")
        if rule_id is None or rule_id not in by_id:
            adds.append(Operation("add", rule=_payload(rule)))
            continue
        seen.add(rule_id)
        old = by_id[rule_id]
        patch = {k: v for k, v in _payload(rule).items() if not _same_value(old.get(k), v)}
        if patch:
            updates.append(Operation("update", rule_id=rule_id, rule=patch))
    removes = [Operation("remove", rule_id=rid) for rid in by_id if rid not in seen]
    return removes + updates + adds


def next_change(
    rules: list[dict[str, Any]], now: datetime
) -> tuple[datetime, dict[str, Any]] | None:
    """Return the next time an enabled weekly rule fires after ``now``."""
    best: tuple[datetime, dict[str, Any]] | None = None
    for rule in rules:
        if not rule.get("enable", True):
            continue
        parsed = parse_timespec(rule.get("timespec", ""))
        if parsed is None:
            continue
        hour, minute = normalize_time(parsed[0])
        days = {WEEKDAYS.index(d) for d in parsed[1]}
        for offset in range(8):
            day = now + timedelta(days=offset)
            if day.weekday() not in days:
                continue
            when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if when <= now:
                continue
            if best is None or when < best[0]:
                best = (when, rule)
            break
    return best


def current_rule(rules: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Return the enabled weekly rule that fired last at or before ``now``."""
    best: tuple[datetime, dict[str, Any]] | None = None
    for rule in rules:
        if not rule.get("enable", True):
            continue
        parsed = parse_timespec(rule.get("timespec", ""))
        if parsed is None:
            continue
        hour, minute = normalize_time(parsed[0])
        days = {WEEKDAYS.index(d) for d in parsed[1]}
        for offset in range(8):
            day = now - timedelta(days=offset)
            if day.weekday() not in days:
                continue
            when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if when > now:
                continue
            if best is None or when > best[0]:
                best = (when, rule)
            break
    return best[1] if best else None
