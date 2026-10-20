"""Edit the on-device schedule of Shelly BLU TRVs from Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .config_flow import shelly_connection
from .const import CARD_FILENAME, CARD_URL_BASE, CONF_SHELLY_ENTRY_ID, DOMAIN
from .coordinator import ScheduleConfigEntry, TrvScheduleCoordinator
from .rpc import GatewayAuthError, GatewayClient, GatewayError
from .services import async_setup_services
from .websocket import async_setup_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card, services and websocket commands."""
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False
            )
        ]
    )
    # Load the card on every page so it can be used without adding a
    # Lovelace resource by hand.
    add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={version}")
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


def _connection(hass: HomeAssistant, entry: ScheduleConfigEntry) -> dict:
    """Prefer the (possibly updated) connection data of the Shelly entry."""
    shelly_entry_id = entry.data.get(CONF_SHELLY_ENTRY_ID)
    if shelly_entry_id and (shelly := hass.config_entries.async_get_entry(shelly_entry_id)):
        return shelly_connection(shelly)
    return {
        CONF_HOST: entry.data[CONF_HOST],
        CONF_PORT: entry.data.get(CONF_PORT, 80),
        CONF_PASSWORD: entry.data.get(CONF_PASSWORD),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Set up one BLU gateway."""
    conn = _connection(hass, entry)
    client = GatewayClient(
        async_get_clientsession(hass), conn[CONF_HOST], conn[CONF_PORT], conn[CONF_PASSWORD]
    )
    try:
        info = await client.call("Shelly.GetDeviceInfo")
    except GatewayAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GatewayError as err:
        raise ConfigEntryNotReady(f"Gateway {conn[CONF_HOST]} not reachable: {err}") from err

    coordinator = TrvScheduleCoordinator(hass, entry, client, str(info["mac"]).upper())
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
