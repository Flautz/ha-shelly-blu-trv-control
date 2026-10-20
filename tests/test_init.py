"""Integration tests against the fake gateway."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.shelly_blu_trv_schedule.const import CONF_SHELLY_ENTRY_ID, DOMAIN
from custom_components.shelly_blu_trv_schedule.schedule import current_rule

from .conftest import ADDR, GW_MAC

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SENSOR = "sensor.shellyblutrv_f84477000001_schedule"
SENSOR2 = "sensor.shellyblutrv_f84477000002_schedule"
SWITCH = "switch.shellyblutrv_f84477000001_schedule_active"
CLIMATE = "climate.shellyblutrv_f84477000001_thermostat"
CLIMATE2 = "climate.shellyblutrv_f84477000002_thermostat"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="gw",
        unique_id=GW_MAC,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 80, CONF_PASSWORD: None},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_manual(hass: HomeAssistant, gateway) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "1.2.3.4", CONF_PORT: 80}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == GW_MAC
    assert result["data"][CONF_HOST] == "1.2.3.4"


async def test_config_flow_from_shelly_entry(hass: HomeAssistant, gateway) -> None:
    shelly = MockConfigEntry(
        domain="shelly",
        title="shellyblugwg3",
        unique_id="a0b1c2d3e4f5",
        data={CONF_HOST: "1.2.3.4", "model": "S3GW-1DBT001", "gen": 3},
    )
    shelly.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SHELLY_ENTRY_ID: shelly.entry_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SHELLY_ENTRY_ID: shelly.entry_id}


async def test_config_flow_not_a_gateway(hass: HomeAssistant, gateway) -> None:
    async def no_blu(*args, **kwargs):
        return {"methods": ["Switch.Set"]}

    orig = gateway.call

    async def call(method, params=None, timeout=0):
        if method == "Shelly.ListMethods":
            return await no_blu()
        return await orig(method, params, timeout)

    gateway.call = call
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "1.2.3.4"}
    )
    assert result["errors"] == {"base": "not_a_gateway"}


async def test_setup_and_sensor(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(SENSOR)
    assert state is not None
    attrs = state.attributes
    assert attrs["loaded"] is True
    assert attrs["address"] == ADDR
    assert [r["time"] for r in attrs["rules"]] == ["08:00", "16:00"]
    assert attrs["rules"][0]["days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert state.state not in ("unknown", "unavailable")
    assert hass.states.get(SENSOR2).attributes["rules"] == []
    assert hass.states.get(SWITCH).state == "on"
    # TRV without rules: switch not available
    assert (
        hass.states.get("switch.shellyblutrv_f84477000002_schedule_active").state == "unavailable"
    )

    # Without the Shelly integration an own device is created
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDR)})
    assert dev is not None and dev.model == "Shelly BLU TRV"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_schedule_read_only_when_revision_changes(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    trv = gateway.trv()
    lists = trv.calls.count("TRV.ListScheduleRules")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert trv.calls.count("TRV.ListScheduleRules") == lists

    # Change made in the Shelly app -> revision bump -> re-read
    trv.rules[0]["target_C"] = 22.0
    trv.rev += 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done()
    assert trv.calls.count("TRV.ListScheduleRules") == lists + 1
    assert hass.states.get(SENSOR).attributes["rules"][0]["target_C"] == 22.0


async def test_websocket_get_set(hass: HomeAssistant, gateway, hass_ws_client) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": f"{DOMAIN}/list"})
    msg = await client.receive_json()
    assert {t["entity_id"] for t in msg["result"]} == {SENSOR, SENSOR2}

    await client.send_json_auto_id({"type": f"{DOMAIN}/get", "entity_id": SENSOR, "refresh": True})
    msg = await client.receive_json()
    assert msg["success"]
    rules = msg["result"]["rules"]
    assert msg["result"]["max_target_C"] == 30.0

    # Edit: change first rule, drop the second, add a new one.
    new_rules = [
        {
            "rule_id": rules[0]["rule_id"],
            "enable": True,
            "time": "06:30",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "target_C": 21.5,
        },
        {"enable": True, "time": "22:00", "days": ["mon", "sun"], "target_C": 17},
    ]
    gateway.trv().calls.clear()
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/set", "entity_id": SENSOR, "rules": new_rules}
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    assert gateway.trv().calls == [
        "TRV.ListScheduleRules",
        "TRV.RemoveScheduleRule",
        "TRV.UpdateScheduleRule",
        "TRV.AddScheduleRule",
        "TRV.ListScheduleRules",
    ]
    assert gateway.trv().rules == [
        {
            "rule_id": 0,
            "enable": True,
            "target_C": 21.5,
            "timespec": "0 30 6 * * MON,TUE,WED,THU,FRI",
        },
        {"rule_id": 1, "enable": True, "target_C": 17.0, "timespec": "0 0 22 * * SUN,MON"},
    ]
    assert [r["time"] for r in msg["result"]["rules"]] == ["06:30", "22:00"]
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).attributes["rules"][1]["days"] == ["mon", "sun"]

    # Validation errors are reported without touching the TRV
    gateway.trv().calls.clear()
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/set",
            "entity_id": SENSOR,
            "rules": [{"time": "06:30", "days": ["mon"], "target_C": 40}],
        }
    )
    msg = await client.receive_json()
    assert not msg["success"] and "40" in msg["error"]["message"]
    assert gateway.trv().calls == []


async def test_attach_to_shelly_device(hass: HomeAssistant, gateway, hass_ws_client) -> None:
    """Entities go onto the Shelly integration's TRV device; its climate entity works too."""
    shelly = MockConfigEntry(domain="shelly", data={CONF_HOST: "1.2.3.4"})
    shelly.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=shelly.entry_id,
        identifiers={("shelly", ADDR)},
        name="shellyblutrv-f84477000001",
        model="Shelly BLU TRV",
    )
    dev_reg.async_update_device(device.id, name_by_user="Schlafzimmer")
    climate = er.async_get(hass).async_get_or_create(
        "climate", "shelly", f"{ADDR}-blutrv:200", device_id=device.id, config_entry=shelly
    )
    await _setup(hass)

    ent = er.async_get(hass).async_get("sensor.schlafzimmer_schedule")
    assert ent is not None and ent.device_id == device.id
    assert er.async_get(hass).async_get("switch.schlafzimmer_schedule_active") is not None
    # The integration does not create an own device for this TRV
    assert dev_reg.async_get_device(identifiers={(DOMAIN, ADDR)}) is None

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/get", "entity_id": climate.entity_id})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["address"] == ADDR


async def test_device_error_rolls_back_to_device_state(
    hass: HomeAssistant, gateway, hass_ws_client
) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    gateway.trv().fail_next = "TRV.AddScheduleRule"
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/set",
            "entity_id": SENSOR,
            "rules": [{"time": "05:00", "days": "all", "target_C": 19}],
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    # Both old rules were removed before the add failed; the cache shows that.
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).attributes["rules"] == []


async def test_services(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    trv = gateway.trv()

    await hass.services.async_call(
        DOMAIN,
        "add_rule",
        {"entity_id": SENSOR, "time": "12:00", "days": ["sat", "sun"], "target_C": 19},
        blocking=True,
    )
    assert trv.rules[-1] == {
        "rule_id": 2,
        "enable": True,
        "target_C": 19.0,
        "timespec": "0 0 12 * * SUN,SAT",
    }

    await hass.services.async_call(
        DOMAIN, "update_rule", {"entity_id": SENSOR, "rule_id": 2, "time": "13:15"}, blocking=True
    )
    assert trv.rules[-1]["timespec"] == "0 15 13 * * SUN,SAT"
    await hass.services.async_call(
        DOMAIN, "update_rule", {"entity_id": SENSOR, "rule_id": 2, "pos": 50}, blocking=True
    )
    assert trv.rules[-1] == {
        "rule_id": 2,
        "enable": True,
        "pos": 50,
        "timespec": "0 15 13 * * SUN,SAT",
    }

    await hass.services.async_call(
        DOMAIN, "remove_rule", {"entity_id": SENSOR, "rule_id": 2}, blocking=True
    )
    assert [r["rule_id"] for r in trv.rules] == [0, 1]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "remove_rule", {"entity_id": SENSOR, "rule_id": 9}, blocking=True
        )

    result = await hass.services.async_call(
        DOMAIN, "get_schedule", {"entity_id": [SENSOR]}, blocking=True, return_response=True
    )
    assert len(result[SENSOR]["rules"]) == 2

    await hass.services.async_call(
        DOMAIN, "copy_schedule", {"source": SENSOR, "entity_id": [SENSOR2]}, blocking=True
    )
    assert [{k: v for k, v in r.items() if k != "rule_id"} for r in gateway.trv(201).rules] == [
        {k: v for k, v in r.items() if k != "rule_id"} for r in trv.rules
    ]

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {"entity_id": [SENSOR2], "rules": [{"time": "7:00", "days": "weekdays", "target_C": 20}]},
        blocking=True,
    )
    assert gateway.trv(201).rules == [
        {
            "rule_id": 0,
            "enable": True,
            "target_C": 20.0,
            "timespec": "0 0 7 * * MON,TUE,WED,THU,FRI",
        }
    ]


async def test_pause_switch(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    trv = gateway.trv()
    trv.rules[1]["enable"] = False
    trv.rev += 1

    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    assert [r["enable"] for r in trv.rules] == [False, False]
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(SENSOR).state == "unknown"

    # Turning on restores only the rules that were on before.
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert [r["enable"] for r in trv.rules] == [True, False]
    assert hass.states.get(SWITCH).state == "on"


async def _climate(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "climate", service, {"entity_id": data.pop("entity_id", CLIMATE), **data}, blocking=True
    )


async def test_thermostat_modes(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    trv = gateway.trv()
    state = hass.states.get(CLIMATE)
    assert state.state == "auto"
    assert state.attributes["hvac_modes"] == ["auto", "heat", "off"]
    assert state.attributes["temperature"] == 8.0

    # Manual: the schedule is paused, the target stays.
    trv.calls.clear()
    await _climate(hass, "set_hvac_mode", hvac_mode="heat")
    assert [r["enable"] for r in trv.rules] == [False, False]
    assert "TRV.SetTarget" not in trv.calls
    assert hass.states.get(CLIMATE).state == "heat"
    assert hass.states.get(SWITCH).state == "off"

    await _climate(hass, "set_temperature", temperature=21.5)
    assert trv.target == 21.5
    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes["temperature"]) == ("heat", 21.5)

    # Off: minimum target; heat restores the previous target.
    await _climate(hass, "set_hvac_mode", hvac_mode="off")
    state = hass.states.get(CLIMATE)
    assert trv.target == 4.0
    assert (state.state, state.attributes["hvac_action"]) == ("off", "off")
    await _climate(hass, "set_hvac_mode", hvac_mode="heat")
    assert trv.target == 21.5
    assert hass.states.get(CLIMATE).state == "heat"

    # Turning the ring above the minimum while off counts as manual.
    await _climate(hass, "turn_off")
    assert hass.states.get(CLIMATE).state == "off"
    await _climate(hass, "set_temperature", temperature=18)
    assert hass.states.get(CLIMATE).state == "heat"

    # Auto: rules on again and the current period applied.
    await _climate(hass, "set_hvac_mode", hvac_mode="auto")
    assert [r["enable"] for r in trv.rules] == [True, True]
    expected = current_rule(trv.rules, dt_util.now())["target_C"]
    state = hass.states.get(CLIMATE)
    assert trv.target == expected
    assert (state.state, state.attributes["temperature"]) == ("auto", expected)

    # Off from auto and back on restores the schedule.
    await _climate(hass, "turn_off")
    assert [r["enable"] for r in trv.rules] == [False, False]
    await _climate(hass, "turn_on")
    assert [r["enable"] for r in trv.rules] == [True, True]
    assert hass.states.get(CLIMATE).state == "auto"


async def test_thermostat_mode_survives_poll(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    await _climate(hass, "set_hvac_mode", hvac_mode="off")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()
    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes["temperature"]) == ("off", 4.0)


async def test_thermostat_without_schedule(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    state = hass.states.get(CLIMATE2)
    assert state.attributes["hvac_modes"] == ["heat", "off"]
    assert state.state == "heat"
    with pytest.raises(ServiceValidationError):
        await _climate(hass, "set_hvac_mode", entity_id=CLIMATE2, hvac_mode="auto")
    await _climate(hass, "turn_off", entity_id=CLIMATE2)
    assert hass.states.get(CLIMATE2).state == "off"
    await _climate(hass, "turn_on", entity_id=CLIMATE2)
    # No target was remembered above the minimum before: 8 °C was.
    assert gateway.trv(201).target == 8.0
    assert hass.states.get(CLIMATE2).state == "heat"


async def test_thermostat_rejects_out_of_range(hass: HomeAssistant, gateway) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _climate(hass, "set_temperature", temperature=35)
    assert gateway.trv().target == 8.0


async def test_thermostat_follows_shelly_climate(hass: HomeAssistant, gateway) -> None:
    """Temperatures come from the Shelly climate entity (pushed updates)."""
    shelly = MockConfigEntry(domain="shelly", data={CONF_HOST: "1.2.3.4"})
    shelly.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=shelly.entry_id,
        identifiers={("shelly", ADDR)},
        name="shellyblutrv-f84477000001",
    )
    dev_reg.async_update_device(device.id, name_by_user="Schlafzimmer")
    source = er.async_get(hass).async_get_or_create(
        "climate", "shelly", f"{ADDR}-blutrv:200", device_id=device.id, config_entry=shelly
    )
    attrs = {"temperature": 8.0, "current_temperature": 20.5, "hvac_action": "heating"}
    hass.states.async_set(source.entity_id, "heat", attrs)
    await _setup(hass)

    entity_id = "climate.schlafzimmer_thermostat"
    state = hass.states.get(entity_id)
    assert state.attributes["current_temperature"] == 20.5
    assert state.attributes["hvac_action"] == "heating"

    hass.states.async_set(source.entity_id, "heat", {**attrs, "current_temperature": 21.0})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["current_temperature"] == 21.0

    # The target we sent is shown until the device reports a new one ...
    await _climate(hass, "set_temperature", entity_id=entity_id, temperature=22)
    assert hass.states.get(entity_id).attributes["temperature"] == 22.0
    hass.states.async_set(source.entity_id, "heat", {**attrs, "current_temperature": 21.2})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 22.0
    # ... after that the device wins (e.g. the ring was turned).
    hass.states.async_set(source.entity_id, "heat", {**attrs, "temperature": 22.0})
    await hass.async_block_till_done()
    hass.states.async_set(source.entity_id, "heat", {**attrs, "temperature": 19.0})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 19.0


async def test_failed_schedule_read_backs_off(hass: HomeAssistant, gateway, freezer) -> None:
    """An unreachable TRV is not retried every poll (that blocks the gateway)."""
    trv = gateway.trv(201)
    trv.fail_always = "TRV.ListScheduleRules"
    await _setup(hass)
    assert trv.calls.count("TRV.ListScheduleRules") == 1

    async def poll(minutes: int) -> int:
        for _ in range(minutes):
            freezer.tick(timedelta(minutes=1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
        return trv.calls.count("TRV.ListScheduleRules")

    assert await poll(1) == 1
    assert await poll(1) == 2  # 2 min after the first failure
    assert await poll(3) == 2
    assert await poll(1) == 3  # then 4 min
    trv.fail_always = None
    assert await poll(8) == 4  # 8 min, succeeds
    assert hass.states.get(SENSOR2).attributes["loaded"] is True
    # The other TRV was read normally.
    assert hass.states.get(SENSOR).attributes["loaded"] is True
