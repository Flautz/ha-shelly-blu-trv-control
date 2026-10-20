"""Helpers shared by services and the websocket API."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import TrvScheduleCoordinator, TrvState
from .schedule import describe_rule, sort_rules


def resolve_trv(hass: HomeAssistant, entity_id: str) -> tuple[TrvScheduleCoordinator, TrvState]:
    """Find the TRV for one of our entities or any entity on the same device.

    This allows passing e.g. the Shelly climate entity of the thermostat.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is not None and entry.platform != DOMAIN and entry.device_id:
        entry = next(
            (
                e
                for e in er.async_entries_for_device(registry, entry.device_id)
                if e.platform == DOMAIN
            ),
            None,
        )
    if entry is None or entry.platform != DOMAIN or not entry.config_entry_id:
        raise ServiceValidationError(
            f"{entity_id} is not a Shelly BLU TRV handled by {DOMAIN}",
            translation_domain=DOMAIN,
            translation_key="unknown_trv",
            translation_placeholders={"entity_id": entity_id},
        )
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            f"The gateway of {entity_id} is not loaded",
            translation_domain=DOMAIN,
            translation_key="not_loaded",
            translation_placeholders={"entity_id": entity_id},
        )
    coordinator: TrvScheduleCoordinator = config_entry.runtime_data
    addr = entry.unique_id.split("-")[1]
    trv = coordinator.by_addr(addr)
    if trv is None:
        raise ServiceValidationError(
            f"{entity_id} is currently not paired with the gateway",
            translation_domain=DOMAIN,
            translation_key="unknown_trv",
            translation_placeholders={"entity_id": entity_id},
        )
    return coordinator, trv


def schedule_payload(trv: TrvState) -> dict[str, Any]:
    """Return the schedule of a TRV in API form."""
    return {
        "trv_id": trv.trv_id,
        "address": trv.addr,
        "connected": trv.connected,
        "loaded": trv.rules is not None,
        "schedule_rev": trv.rules_rev,
        "min_target_C": trv.limits.min_c,
        "max_target_C": trv.limits.max_c,
        "error": trv.error,
        "rules": [describe_rule(r) for r in sort_rules(trv.rules or [])],
    }
