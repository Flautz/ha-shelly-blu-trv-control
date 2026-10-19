"""Switch to pause/resume the schedule of a Shelly BLU TRV."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ScheduleConfigEntry
from .entity import TrvEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScheduleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a schedule switch per TRV (also for TRVs paired later)."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [t for t in coordinator.trvs.values() if t.addr not in known]
        known.update(t.addr for t in new)
        if new:
            async_add_entities(TrvScheduleSwitch(coordinator, t) for t in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class TrvScheduleSwitch(TrvEntity, SwitchEntity):
    """On while at least one schedule rule is enabled."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, trv) -> None:
        super().__init__(coordinator, trv, "schedule_enabled")

    @property
    def available(self) -> bool:
        """Only available once the rules have been read."""
        return super().available and self.trv.rules is not None and bool(self.trv.rules)

    @property
    def is_on(self) -> bool | None:
        """Return True if any rule is enabled."""
        trv = self.trv
        if trv is None or trv.rules is None:
            return None
        return any(r.get("enable", True) for r in trv.rules)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Re-enable the rules that were active before pausing."""
        await self.coordinator.async_set_paused(self._trv_id, False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable all rules."""
        await self.coordinator.async_set_paused(self._trv_id, True)
