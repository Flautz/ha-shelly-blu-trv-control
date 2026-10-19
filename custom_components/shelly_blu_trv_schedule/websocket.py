"""Websocket API used by the schedule card."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helpers import resolve_trv, schedule_payload
from .rpc import GatewayError


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register websocket commands."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_set)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
@callback
def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List all TRVs with their schedule sensor."""
    registry = er.async_get(hass)
    trvs = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            if ent.domain != "sensor" or not ent.unique_id.endswith("-schedule"):
                continue
            trvs.append({"entity_id": ent.entity_id, "device_id": ent.device_id})
    connection.send_result(msg["id"], trvs)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("refresh", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the schedule of a TRV, reading it from the device if needed."""
    try:
        coordinator, trv = resolve_trv(hass, msg["entity_id"])
        if msg["refresh"] or trv.rules is None:
            try:
                await coordinator.async_fetch_rules(trv.trv_id)
            except GatewayError as err:
                raise HomeAssistantError(f"Could not read schedule from the TRV: {err}") from err
            finally:
                coordinator.async_update_listeners()
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "schedule_error", str(err))
        return
    connection.send_result(msg["id"], schedule_payload(trv))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("rules"): [dict],
    }
)
@websocket_api.async_response
async def ws_set(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Replace the schedule of a TRV."""
    try:
        coordinator, trv = resolve_trv(hass, msg["entity_id"])
        await coordinator.async_apply_rules(trv.trv_id, msg["rules"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "schedule_error", str(err))
        return
    connection.send_result(msg["id"], schedule_payload(trv))
