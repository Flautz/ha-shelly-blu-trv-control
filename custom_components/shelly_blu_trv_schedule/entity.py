"""Base entity for Shelly BLU TRV Schedule."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrvScheduleCoordinator, TrvState

SHELLY_DOMAIN = "shelly"


def _shelly_device(coordinator: TrvScheduleCoordinator, addr: str) -> dr.DeviceEntry | None:
    """Return the device the Shelly integration created for this TRV."""
    hass = coordinator.hass
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(SHELLY_DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (SHELLY_DOMAIN, addr) in device.identifiers:
                return device
    return None


class TrvEntity(CoordinatorEntity[TrvScheduleCoordinator]):
    """An entity attached to one BLU TRV."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrvScheduleCoordinator, trv: TrvState, key: str) -> None:
        super().__init__(coordinator)
        self._trv_id = trv.trv_id
        self._addr = trv.addr
        self._attr_unique_id = f"{coordinator.gateway_mac}-{trv.addr}-{key}"
        self._attr_translation_key = key
        if (device := _shelly_device(coordinator, trv.addr)) is not None:
            # Show the entities on the device of the Shelly integration (the
            # same way helper integrations attach to foreign devices).
            self.device_entry = device
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, trv.addr)},
                connections={(CONNECTION_BLUETOOTH, trv.addr)},
                manufacturer="Shelly",
                model="Shelly BLU TRV",
                model_id="SBTR-001AEU",
                name=trv.name or f"shellyblutrv-{trv.addr.replace(':', '')}",
                sw_version=trv.fw_ver,
            )

    @property
    def trv(self) -> TrvState | None:
        """Return the current TRV state."""
        trv = self.coordinator.trvs.get(self._trv_id)
        return trv if trv is not None and trv.addr == self._addr else None

    @property
    def available(self) -> bool:
        """Return True if the TRV is known to the gateway."""
        return super().available and self.trv is not None
