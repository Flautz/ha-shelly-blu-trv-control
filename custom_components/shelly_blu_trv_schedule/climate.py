"""Thermostat with auto (schedule), heat (manual) and off for Shelly BLU TRVs."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .coordinator import MODE_AUTO, MODE_HEAT, MODE_OFF, ScheduleConfigEntry
from .entity import SHELLY_DOMAIN, TrvEntity

_TO_HVAC = {MODE_AUTO: HVACMode.AUTO, MODE_HEAT: HVACMode.HEAT, MODE_OFF: HVACMode.OFF}
# How long a target we sent wins over the (older) one reported by the device.
PENDING_TARGET_SECONDS = 120
# HA 2026.11 renamed the temperature properties to native_*.
_NATIVE_API = hasattr(ClimateEntity, "native_target_temperature")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScheduleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a thermostat per TRV (also for TRVs paired later)."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [t for t in coordinator.trvs.values() if t.addr not in known]
        known.update(t.addr for t in new)
        if new:
            async_add_entities(TrvThermostat(coordinator, t) for t in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class TrvThermostat(TrvEntity, ClimateEntity):
    """Auto runs the on-device schedule, heat pauses it, off sets the minimum.

    Temperatures and the heating state are taken from the climate entity of
    the Shelly integration when there is one (it gets pushed updates); the
    gateway is only polled every minute.
    """

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator, trv) -> None:
        super().__init__(coordinator, trv, "thermostat")
        self._source_entity_id: str | None = None
        self._pending_target: float | None = None
        self._pending_since = 0.0

    async def async_added_to_hass(self) -> None:
        """Follow the Shelly climate entity of the same device."""
        await super().async_added_to_hass()
        if self.device_entry is None:
            return
        registry = er.async_get(self.hass)
        self._source_entity_id = next(
            (
                e.entity_id
                for e in er.async_entries_for_device(registry, self.device_entry.id)
                if e.domain == "climate" and e.platform == SHELLY_DOMAIN
            ),
            None,
        )
        if self._source_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._source_entity_id, self._async_source_changed
                )
            )

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        if new is not None and (
            old is None
            or new.attributes.get(ATTR_TEMPERATURE) != old.attributes.get(ATTR_TEMPERATURE)
        ):
            self._pending_target = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        trv = self.trv
        if (
            self._pending_target is not None
            and self._source is None
            and trv is not None
            and trv.target_C == self._pending_target
        ):
            self._pending_target = None
        super()._handle_coordinator_update()

    @property
    def _source(self) -> State | None:
        if self._source_entity_id is None:
            return None
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    @property
    def min_temp(self) -> float:
        """Return the minimum target of the TRV."""
        return self.trv.limits.min_c if self.trv else 4.0

    @property
    def max_temp(self) -> float:
        """Return the maximum target of the TRV."""
        return self.trv.limits.max_c if self.trv else 30.0

    @property
    def native_current_temperature(self) -> float | None:
        """Return the temperature measured by the TRV."""
        if (source := self._source) is not None:
            return source.attributes.get(ATTR_CURRENT_TEMPERATURE)
        return self.trv.current_C if self.trv else None

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target, preferring one just sent over the reported one."""
        if (
            self._pending_target is not None
            and time.monotonic() - self._pending_since < PENDING_TARGET_SECONDS
        ):
            return self._pending_target
        if (source := self._source) is not None:
            return source.attributes.get(ATTR_TEMPERATURE)
        return self.trv.target_C if self.trv else None

    if not _NATIVE_API:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Offer auto only while the TRV has a schedule."""
        trv = self.trv
        if trv is not None and trv.rules == []:
            return [HVACMode.HEAT, HVACMode.OFF]
        return [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return auto, heat or off (unknown until the schedule was read)."""
        trv = self.trv
        if trv is None or (mode := trv.mode(self.native_target_temperature)) is None:
            return None
        return _TO_HVAC[mode]

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return whether the valve is open."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if (source := self._source) is not None:
            return source.attributes.get(ATTR_HVAC_ACTION)
        if self.trv is None or self.trv.pos is None:
            return None
        return HVACAction.HEATING if self.trv.pos else HVACAction.IDLE

    def _set_pending(self, target: float) -> None:
        self._pending_target = target
        self._pending_since = time.monotonic()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target; also switches the mode if one is given."""
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        if (target := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._set_pending(await self.coordinator.async_set_target(self._trv_id, target))
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch between schedule, manual and off."""
        target = await self.coordinator.async_set_mode(
            self._trv_id, str(hvac_mode), self.native_target_temperature
        )
        if target is not None:
            self._set_pending(target)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn on with the schedule if there is one."""
        trv = self.trv
        await self.async_set_hvac_mode(
            HVACMode.HEAT if trv is not None and trv.rules == [] else HVACMode.AUTO
        )

    async def async_turn_off(self) -> None:
        """Turn off (minimum target, schedule paused)."""
        await self.async_set_hvac_mode(HVACMode.OFF)
