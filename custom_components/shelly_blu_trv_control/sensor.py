"""Schedule sensor for Shelly BLU TRVs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ScheduleConfigEntry
from .entity import TrvEntity
from .schedule import describe_rule, next_change, sort_rules


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScheduleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a schedule sensor per TRV (also for TRVs paired later)."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [t for t in coordinator.trvs.values() if t.addr not in known]
        known.update(t.addr for t in new)
        if new:
            async_add_entities(TrvScheduleSensor(coordinator, t) for t in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class TrvScheduleSensor(TrvEntity, SensorEntity):
    """Shows the next scheduled change; the rules are in the attributes."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, trv) -> None:
        super().__init__(coordinator, trv, "schedule")

    @property
    def _next(self):
        trv = self.trv
        if trv is None or trv.rules is None:
            return None
        return next_change(trv.rules, dt_util.now())

    @property
    def native_value(self) -> datetime | None:
        """Return when the schedule changes the target next."""
        nxt = self._next
        return nxt[0] if nxt else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the rules for automations and the card."""
        trv = self.trv
        if trv is None:
            return {}
        attrs: dict[str, Any] = {
            "trv_id": trv.trv_id,
            "address": trv.addr,
            "schedule_rev": trv.rules_rev,
            "min_target_C": trv.limits.min_c,
            "max_target_C": trv.limits.max_c,
            "loaded": trv.rules is not None,
            "rules": [describe_rule(r) for r in sort_rules(trv.rules or [])],
        }
        if nxt := self._next:
            for key in ("target_C", "pos"):
                if key in nxt[1]:
                    attrs[f"next_{key}"] = nxt[1][key]
        if trv.error:
            attrs["error"] = trv.error
        return attrs
