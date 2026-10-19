"""Coordinator for the BLU TRVs behind one Shelly BLU Gateway."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import BLUTRV_PREFIX, DOMAIN, SCAN_INTERVAL
from .rpc import GatewayClient, GatewayError, RpcError
from .schedule import Limits, ScheduleError, normalize_rule, plan_changes

_LOGGER = logging.getLogger(__name__)

type ScheduleConfigEntry = ConfigEntry[TrvScheduleCoordinator]


@dataclass
class TrvState:
    """What we know about one BLU TRV."""

    trv_id: int
    addr: str
    name: str | None = None
    fw_ver: str | None = None
    connected: bool = False
    battery: int | None = None
    target_C: float | None = None
    current_C: float | None = None
    # Revision reported by the TRV status (cheap, cached on the gateway).
    schedule_rev: int | None = None
    # Rules as last read from the TRV and the revision they belong to.
    rules: list[dict[str, Any]] | None = None
    rules_rev: int | None = None
    cfg_rev: int | None = None
    limits: Limits = field(default_factory=Limits)
    error: str | None = None

    @property
    def rules_stale(self) -> bool:
        """Return True if the cached rules need to be re-read from the TRV."""
        return self.rules is None or (
            self.schedule_rev is not None and self.schedule_rev != self.rules_rev
        )


class TrvScheduleCoordinator(DataUpdateCoordinator[dict[int, TrvState]]):
    """Polls the gateway and caches the schedule of every BLU TRV."""

    config_entry: ScheduleConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ScheduleConfigEntry,
        client: GatewayClient,
        gateway_mac: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.gateway_mac = gateway_mac
        self.trvs: dict[int, TrvState] = {}
        self._fetch_task: asyncio.Task | None = None
        self._edit_lock = asyncio.Lock()
        self._store: Store[dict[str, list[int]]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.disabled_rules"
        )
        self._paused: dict[str, list[int]] = {}

    async def _async_setup(self) -> None:
        self._paused = await self._store.async_load() or {}

    def by_addr(self, addr: str) -> TrvState | None:
        """Return the TRV with the given Bluetooth address."""
        addr = addr.lower()
        return next((t for t in self.trvs.values() if t.addr == addr), None)

    async def _async_update_data(self) -> dict[int, TrvState]:
        try:
            components = await self.client.get_components()
        except GatewayError as err:
            raise UpdateFailed(f"Error talking to gateway: {err}") from err

        seen: set[int] = set()
        for comp in components:
            key = comp.get("key", "")
            if not key.startswith(BLUTRV_PREFIX):
                continue
            trv_id = int(key[len(BLUTRV_PREFIX) :])
            cfg = comp.get("config") or {}
            status = comp.get("status") or {}
            addr = str(cfg.get("addr", "")).lower()
            trv = self.trvs.get(trv_id)
            if trv is None or trv.addr != addr:
                trv = self.trvs[trv_id] = TrvState(trv_id=trv_id, addr=addr)
            seen.add(trv_id)
            trv.name = cfg.get("name")
            trv.connected = bool(status.get("connected", False))
            trv.fw_ver = status.get("fw_ver")
            trv.battery = status.get("battery")
            trv.target_C = status.get("target_C")
            trv.current_C = status.get("current_C")
        for trv_id in set(self.trvs) - seen:
            del self.trvs[trv_id]

        for trv in self.trvs.values():
            await self._async_update_remote_status(trv)

        if any(t.rules_stale for t in self.trvs.values()):
            self._schedule_fetch()
        return self.trvs

    async def _async_update_remote_status(self, trv: TrvState) -> None:
        """Read revision counters from the gateway's cached TRV status."""
        try:
            result = await self.client.call("BluTrv.GetRemoteStatus", {"id": trv.trv_id})
            status = result.get("status", {})
            trv.schedule_rev = status.get("trv:0", {}).get("schedule_rev", trv.schedule_rev)
            cfg_rev = status.get("sys", {}).get("cfg_rev")
            if cfg_rev is not None and cfg_rev != trv.cfg_rev:
                result = await self.client.call("BluTrv.GetRemoteConfig", {"id": trv.trv_id})
                trv_cfg = result.get("config", {}).get("trv:0", {})
                trv.limits = Limits(
                    float(trv_cfg.get("min_target_C", trv.limits.min_c)),
                    float(trv_cfg.get("max_target_C", trv.limits.max_c)),
                )
                trv.cfg_rev = cfg_rev
        except (GatewayError, AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug("Could not read remote status of TRV %s: %s", trv.trv_id, err)

    def _schedule_fetch(self) -> None:
        """Read stale schedules in the background (BLE calls are slow)."""
        if self._fetch_task is not None and not self._fetch_task.done():
            return
        self._fetch_task = self.config_entry.async_create_background_task(
            self.hass, self._async_fetch_stale(), f"{DOMAIN} fetch schedules"
        )

    async def _async_fetch_stale(self) -> None:
        for trv in list(self.trvs.values()):
            if not trv.rules_stale:
                continue
            try:
                await self.async_fetch_rules(trv.trv_id)
            except GatewayError as err:
                _LOGGER.debug("Could not read schedule of TRV %s: %s", trv.trv_id, err)
            # Publish each TRV as soon as it is read; BLE reads take seconds.
            self.async_update_listeners()

    def get_trv(self, trv_id: int) -> TrvState:
        """Return a known TRV or raise."""
        if (trv := self.trvs.get(trv_id)) is None:
            raise HomeAssistantError(f"Unknown BLU TRV {trv_id}")
        return trv

    async def async_fetch_rules(self, trv_id: int) -> list[dict[str, Any]]:
        """Read the schedule rules from the TRV (via Bluetooth)."""
        trv = self.get_trv(trv_id)
        try:
            result = await self.client.trv_call(trv_id, "TRV.ListScheduleRules", {"id": 0})
        except GatewayError as err:
            trv.error = str(err)
            raise
        trv.rules = list(result.get("rules", []))
        trv.rules_rev = trv.schedule_rev = result.get("schedule_rev")
        trv.error = None
        return trv.rules

    async def async_apply_rules(
        self, trv_id: int, rules: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace the schedule of a TRV with ``rules`` (minimal RPC calls)."""
        trv = self.get_trv(trv_id)
        try:
            desired = [normalize_rule(rule, trv.limits) for rule in rules]
        except ScheduleError as err:
            raise HomeAssistantError(str(err)) from err

        async with self._edit_lock:
            try:
                current = await self.async_fetch_rules(trv_id)
                operations = plan_changes(current, desired)
            except ScheduleError as err:
                raise HomeAssistantError(str(err)) from err
            except GatewayError as err:
                raise HomeAssistantError(f"Could not read schedule: {err}") from err

            failure: Exception | None = None
            for op in operations:
                try:
                    if op.kind == "remove":
                        await self.client.trv_call(
                            trv_id, "TRV.RemoveScheduleRule", {"id": 0, "rule_id": op.rule_id}
                        )
                    elif op.kind == "update":
                        await self.client.trv_call(
                            trv_id,
                            "TRV.UpdateScheduleRule",
                            {"id": 0, "rule_id": op.rule_id, "rule": op.rule},
                        )
                    else:
                        await self.client.trv_call(
                            trv_id, "TRV.AddScheduleRule", {"id": 0, "rule": op.rule}
                        )
                except GatewayError as err:
                    failure = err
                    break

            if operations:
                try:
                    await self.async_fetch_rules(trv_id)
                except GatewayError as err:
                    failure = failure or err
            self.async_update_listeners()

        if failure is not None:
            if isinstance(failure, RpcError):
                raise HomeAssistantError(f"TRV rejected the change: {failure.message}") from failure
            raise HomeAssistantError(f"Could not save schedule: {failure}") from failure
        return trv.rules or []

    async def async_set_paused(self, trv_id: int, paused: bool) -> None:
        """Disable all rules (remembering which were on) or re-enable them."""
        trv = self.get_trv(trv_id)
        try:
            rules = await self.async_fetch_rules(trv_id)
        except GatewayError as err:
            raise HomeAssistantError(f"Could not read schedule: {err}") from err
        if paused:
            enabled = [r["rule_id"] for r in rules if r.get("enable", True)]
            if not enabled:
                return
            self._paused[trv.addr] = enabled
            new_rules = [{**r, "enable": False} for r in rules]
        else:
            # Re-enable the rules that were on before pausing; if we do not
            # know them (e.g. paused in the Shelly app), enable all.
            ids = {r["rule_id"] for r in rules}
            restore = set(self._paused.get(trv.addr, [])) & ids or ids
            new_rules = [
                {**r, "enable": r.get("enable", True) or r["rule_id"] in restore} for r in rules
            ]
        await self.async_apply_rules(trv_id, new_rules)
        if not paused:
            self._paused.pop(trv.addr, None)
        await self._store.async_save(self._paused)
