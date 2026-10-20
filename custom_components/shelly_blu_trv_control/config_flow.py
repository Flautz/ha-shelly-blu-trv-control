"""Config flow for Shelly BLU TRV Control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_SHELLY_ENTRY_ID, DOMAIN, GATEWAY_MODEL_PREFIX
from .rpc import GatewayAuthError, GatewayClient, GatewayError

_LOGGER = logging.getLogger(__name__)

SHELLY_DOMAIN = "shelly"
MANUAL = "manual"


def find_shelly_gateways(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return Shelly integration entries that are BLU gateways."""
    registry = dr.async_get(hass)
    found = []
    for entry in hass.config_entries.async_entries(SHELLY_DOMAIN):
        if not entry.data.get(CONF_HOST):
            continue
        model = str(entry.data.get("model", ""))
        devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
        if model.startswith(GATEWAY_MODEL_PREFIX) or any(
            (d.model_id or "").startswith(GATEWAY_MODEL_PREFIX)
            or (d.model or "") == "Shelly BLU TRV"
            for d in devices
        ):
            found.append(entry)
    return found


def shelly_connection(entry: ConfigEntry) -> dict[str, Any]:
    """Return host/port/password stored by the Shelly integration."""
    return {
        CONF_HOST: entry.data[CONF_HOST],
        CONF_PORT: entry.data.get(CONF_PORT, 80),
        CONF_PASSWORD: entry.data.get(CONF_PASSWORD),
    }


async def validate_gateway(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Connect to the gateway and return its device info."""
    client = GatewayClient(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data.get(CONF_PORT, 80),
        data.get(CONF_PASSWORD),
    )
    info = await client.call("Shelly.GetDeviceInfo")
    if info.get("auth_en") and not data.get(CONF_PASSWORD):
        raise GatewayAuthError("Password required")
    methods = (await client.call("Shelly.ListMethods")).get("methods", [])
    if "BluTrv.Call" not in methods:
        raise NotAGateway
    return info


class NotAGateway(Exception):
    """The device does not support BLU TRVs."""


class ShellyBluTrvScheduleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a gateway known to the Shelly integration, or enter one manually."""
        gateways = [
            e
            for e in find_shelly_gateways(self.hass)
            if e.unique_id is None
            or e.unique_id.upper() not in self._async_current_ids(include_ignore=False)
        ]
        if not gateways:
            return await self.async_step_manual()

        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input[CONF_SHELLY_ENTRY_ID]
            if choice == MANUAL:
                return await self.async_step_manual()
            shelly_entry = self.hass.config_entries.async_get_entry(choice)
            if shelly_entry is not None:
                result = await self._async_create(
                    shelly_connection(shelly_entry),
                    {CONF_SHELLY_ENTRY_ID: shelly_entry.entry_id},
                    errors,
                )
                if result is not None:
                    return result

        options = [
            SelectOptionDict(value=e.entry_id, label=f"{e.title} ({e.data[CONF_HOST]})")
            for e in gateways
        ]
        options.append(SelectOptionDict(value=MANUAL, label=MANUAL))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SHELLY_ENTRY_ID, default=options[0]["value"]): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            translation_key="gateway",
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter the gateway address manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: user_input.get(CONF_PORT, 80),
                CONF_PASSWORD: user_input.get(CONF_PASSWORD) or None,
            }
            result = await self._async_create(data, data, errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_PORT, default=80): vol.All(
                            vol.Coerce(int), vol.Range(min=1, max=65535)
                        ),
                        vol.Optional(CONF_PASSWORD): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def _async_create(
        self, connection: dict[str, Any], entry_data: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        try:
            info = await validate_gateway(self.hass, connection)
        except GatewayAuthError:
            errors["base"] = "invalid_auth"
            return None
        except NotAGateway:
            errors["base"] = "not_a_gateway"
            return None
        except GatewayError:
            errors["base"] = "cannot_connect"
            return None
        except Exception:
            _LOGGER.exception("Unexpected error validating gateway")
            errors["base"] = "unknown"
            return None

        await self.async_set_unique_id(str(info["mac"]).upper())
        self._abort_if_unique_id_configured(updates=entry_data)
        return self.async_create_entry(title=info.get("name") or info["id"], data=entry_data)
