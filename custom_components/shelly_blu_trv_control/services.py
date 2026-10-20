"""Services for Shelly BLU TRV Control."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DAYS,
    ATTR_ENABLE,
    ATTR_POS,
    ATTR_RULE_ID,
    ATTR_RULES,
    ATTR_SOURCE,
    ATTR_TARGET_C,
    ATTR_TIME,
    ATTR_TIMESPEC,
    DOMAIN,
    SERVICE_ADD_RULE,
    SERVICE_COPY_SCHEDULE,
    SERVICE_GET_SCHEDULE,
    SERVICE_REFRESH,
    SERVICE_REMOVE_RULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_UPDATE_RULE,
)
from .helpers import resolve_trv, schedule_payload
from .rpc import GatewayError
from .schedule import MAX_RULES, ScheduleError, normalize_days, normalize_time, parse_timespec

_DAYS = vol.All(cv.ensure_list, [cv.string])

RULE_FIELDS = {
    vol.Optional(ATTR_RULE_ID): vol.Coerce(int),
    vol.Optional(ATTR_ENABLE): cv.boolean,
    vol.Optional(ATTR_TIME): cv.string,
    vol.Optional(ATTR_DAYS): _DAYS,
    vol.Optional(ATTR_TIMESPEC): cv.string,
    vol.Optional(ATTR_TARGET_C): vol.Coerce(float),
    vol.Optional(ATTR_POS): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
}
RULE_SCHEMA = vol.Schema(RULE_FIELDS)

ENTITIES = {vol.Required(ATTR_ENTITY_ID): cv.entity_ids}

SCHEMAS = {
    SERVICE_GET_SCHEDULE: vol.Schema(
        {**ENTITIES, vol.Optional("refresh", default=False): cv.boolean}
    ),
    SERVICE_SET_SCHEDULE: vol.Schema(
        {**ENTITIES, vol.Required(ATTR_RULES): vol.All(cv.ensure_list, [RULE_SCHEMA])}
    ),
    SERVICE_ADD_RULE: vol.Schema({**ENTITIES, **RULE_FIELDS}),
    SERVICE_UPDATE_RULE: vol.Schema(
        {**ENTITIES, **RULE_FIELDS, vol.Required(ATTR_RULE_ID): vol.Coerce(int)}
    ),
    SERVICE_REMOVE_RULE: vol.Schema({**ENTITIES, vol.Required(ATTR_RULE_ID): vol.Coerce(int)}),
    SERVICE_COPY_SCHEDULE: vol.Schema({**ENTITIES, vol.Required(ATTR_SOURCE): cv.entity_id}),
    SERVICE_REFRESH: vol.Schema(ENTITIES),
}


def _check(func, *args):
    try:
        return func(*args)
    except ScheduleError as err:
        raise HomeAssistantError(str(err)) from err


def _merge(rule: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update (time/days/timespec/target/pos) to a rule."""
    merged = dict(rule)
    if ATTR_TIMESPEC in changes:
        merged.pop(ATTR_TIME, None)
        merged.pop(ATTR_DAYS, None)
    if ATTR_TIME in changes or ATTR_DAYS in changes:
        # Rebuild the timespec from the existing rule where only one is given.
        parsed = parse_timespec(rule.get(ATTR_TIMESPEC, ""))
        if parsed is None and not (ATTR_TIME in changes and ATTR_DAYS in changes):
            raise HomeAssistantError("This rule uses an advanced timespec; give both time and days")
        merged[ATTR_TIME] = changes.get(ATTR_TIME, parsed[0] if parsed else None)
        merged[ATTR_DAYS] = changes.get(ATTR_DAYS, parsed[1] if parsed else None)
        merged.pop(ATTR_TIMESPEC, None)
    if ATTR_TARGET_C in changes:
        merged.pop(ATTR_POS, None)
    if ATTR_POS in changes:
        merged.pop(ATTR_TARGET_C, None)
    merged.update({k: v for k, v in changes.items() if k not in (ATTR_TIME, ATTR_DAYS)})
    return merged


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services."""

    async def get_schedule(call: ServiceCall) -> ServiceResponse:
        result: dict[str, Any] = {}
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            if call.data["refresh"] or trv.rules is None:
                await _fetch(coordinator, trv.trv_id)
            result[entity_id] = schedule_payload(trv)
        return result

    async def set_schedule(call: ServiceCall) -> None:
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            await coordinator.async_apply_rules(trv.trv_id, call.data[ATTR_RULES])

    async def add_rule(call: ServiceCall) -> None:
        rule = {k: v for k, v in call.data.items() if k != ATTR_ENTITY_ID and k != ATTR_RULE_ID}
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            current = await _fetch(coordinator, trv.trv_id)
            if len(current) >= MAX_RULES:
                raise HomeAssistantError(f"{entity_id} already has {MAX_RULES} rules")
            await coordinator.async_apply_rules(trv.trv_id, [*current, rule])

    async def update_rule(call: ServiceCall) -> None:
        rule_id = call.data[ATTR_RULE_ID]
        changes = {k: v for k, v in call.data.items() if k not in (ATTR_ENTITY_ID, ATTR_RULE_ID)}
        if ATTR_TIME in changes:
            _check(normalize_time, changes[ATTR_TIME])
        if ATTR_DAYS in changes:
            _check(normalize_days, changes[ATTR_DAYS])
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            current = await _fetch(coordinator, trv.trv_id)
            if not any(r[ATTR_RULE_ID] == rule_id for r in current):
                raise HomeAssistantError(f"{entity_id} has no rule {rule_id}")
            rules = [_merge(r, changes) if r[ATTR_RULE_ID] == rule_id else r for r in current]
            await coordinator.async_apply_rules(trv.trv_id, rules)

    async def remove_rule(call: ServiceCall) -> None:
        rule_id = call.data[ATTR_RULE_ID]
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            current = await _fetch(coordinator, trv.trv_id)
            if not any(r[ATTR_RULE_ID] == rule_id for r in current):
                raise HomeAssistantError(f"{entity_id} has no rule {rule_id}")
            await coordinator.async_apply_rules(
                trv.trv_id, [r for r in current if r[ATTR_RULE_ID] != rule_id]
            )

    async def copy_schedule(call: ServiceCall) -> None:
        src_coordinator, src = resolve_trv(hass, call.data[ATTR_SOURCE])
        rules = [
            {k: v for k, v in r.items() if k != ATTR_RULE_ID}
            for r in await _fetch(src_coordinator, src.trv_id)
        ]
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            if trv is src:
                continue
            await coordinator.async_apply_rules(trv.trv_id, rules)

    async def refresh(call: ServiceCall) -> None:
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, trv = resolve_trv(hass, entity_id)
            await _fetch(coordinator, trv.trv_id)
            coordinator.async_update_listeners()

    handlers = {
        SERVICE_SET_SCHEDULE: set_schedule,
        SERVICE_ADD_RULE: add_rule,
        SERVICE_UPDATE_RULE: update_rule,
        SERVICE_REMOVE_RULE: remove_rule,
        SERVICE_COPY_SCHEDULE: copy_schedule,
        SERVICE_REFRESH: refresh,
    }
    for name, handler in handlers.items():
        hass.services.async_register(DOMAIN, name, handler, schema=SCHEMAS[name])
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        get_schedule,
        schema=SCHEMAS[SERVICE_GET_SCHEDULE],
        supports_response=SupportsResponse.ONLY,
    )


async def _fetch(coordinator, trv_id: int) -> list[dict[str, Any]]:
    try:
        return await coordinator.async_fetch_rules(trv_id)
    except GatewayError as err:
        raise HomeAssistantError(f"Could not read schedule from the TRV: {err}") from err
