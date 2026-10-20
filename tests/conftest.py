"""Fixtures: a fake BLU Gateway that behaves like the real one."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.shelly_blu_trv_schedule.rpc import GatewayConnectionError, RpcError

GW_MAC = "A0B1C2D3E4F5"
ADDR = "f8:44:77:00:00:01"
ADDR2 = "f8:44:77:00:00:02"

APP_DAYS = "SUN,MON,TUE,WED,THU,FRI,SAT"


class FakeTrv:
    """Schedule storage of one TRV, mimicking firmware 1.5.1."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = copy.deepcopy(rules)
        self.rev = 7
        self.target = 8.0
        self.calls: list[str] = []
        self.fail_next: str | None = None
        self.fail_always: str | None = None

    def call(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append(method)
        if self.fail_next == method:
            self.fail_next = None
            raise GatewayConnectionError("BLE timeout")
        if self.fail_always == method:
            raise RpcError(504, "Timeout (queue)")
        if method == "TRV.SetTarget":
            if not 4 <= params["target_C"] <= 30:
                return {"code": -103, "message": "Argument `target_C` is invalid"}
            self.target = params["target_C"]
            return None
        if method == "TRV.ListScheduleRules":
            return {"schedule_rev": self.rev, "rules": copy.deepcopy(self.rules)}
        if method == "TRV.AddScheduleRule":
            if len(self.rules) >= 10:
                return {"code": -109, "message": "No free slots"}
            rule = params["rule"]
            if err := self._invalid(rule):
                return err
            rule_id = next(i for i in range(10) if all(r["rule_id"] != i for r in self.rules))
            self.rules.append(
                {
                    "rule_id": rule_id,
                    "enable": rule.get("enable", True),
                    **{k: v for k, v in rule.items() if k != "enable"},
                }
            )
            self.rev += 1
            return {"rule_id": rule_id}
        if method == "TRV.UpdateScheduleRule":
            rule = next((r for r in self.rules if r["rule_id"] == params["rule_id"]), None)
            if rule is None:
                return {"code": -105, "message": "Argument `rule_id` not found"}
            if err := self._invalid(params["rule"]):
                return err
            if "pos" in params["rule"]:
                rule.pop("target_C", None)
            if "target_C" in params["rule"]:
                rule.pop("pos", None)
            rule.update(params["rule"])
            self.rev += 1
            return None
        if method == "TRV.RemoveScheduleRule":
            before = len(self.rules)
            self.rules = [r for r in self.rules if r["rule_id"] != params["rule_id"]]
            if len(self.rules) == before:
                return {"code": -105, "message": "Argument `rule_id` not found"}
            self.rev += 1
            return None
        raise RpcError(404, f"No handler for {method}")

    @staticmethod
    def _invalid(rule: dict[str, Any]) -> dict[str, Any] | None:
        if "target_C" in rule and not 4 <= rule["target_C"] <= 30:
            return {"code": -103, "message": "Argument `target_C` is invalid"}
        if "timespec" in rule and len(rule["timespec"].split()) != 6:
            return {"code": -103, "message": "Argument `timespec` is invalid"}
        return None


class FakeGateway:
    """Answers the gateway RPCs the integration uses."""

    def __init__(self) -> None:
        self.trvs = {
            200: (
                ADDR,
                FakeTrv(
                    [
                        {
                            "rule_id": 0,
                            "enable": True,
                            "target_C": 21.0,
                            "timespec": f"0 0 8 * * {APP_DAYS}",
                        },
                        {
                            "rule_id": 1,
                            "enable": True,
                            "target_C": 8.0,
                            "timespec": f"0 0 16 * * {APP_DAYS}",
                        },
                    ]
                ),
            ),
            201: (ADDR2, FakeTrv([])),
        }

    def trv(self, trv_id: int = 200) -> FakeTrv:
        return self.trvs[trv_id][1]

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 0
    ) -> Any:
        params = params or {}
        if method == "Shelly.GetDeviceInfo":
            return {
                "id": "shellyblugwg3-a0b1c2d3e4f5",
                "mac": GW_MAC,
                "model": "S3GW-1DBT001",
                "gen": 3,
                "auth_en": False,
                "name": None,
            }
        if method == "Shelly.ListMethods":
            return {"methods": ["Shelly.GetComponents", "BluTrv.Call", "BluTrv.GetRemoteStatus"]}
        if method == "Shelly.GetComponents":
            comps = [{"key": "ble", "status": {}, "config": {}}]
            for trv_id, (addr, trv) in self.trvs.items():
                comps.append(
                    {
                        "key": f"blutrv:{trv_id}",
                        "status": {
                            "id": trv_id,
                            "target_C": trv.target,
                            "current_C": 19.0,
                            "pos": 0,
                            "connected": True,
                            "battery": 75,
                            "fw_ver": "v1.5.1",
                        },
                        "config": {"id": trv_id, "addr": addr, "name": None},
                    }
                )
            off = params.get("offset", 0)
            return {"components": comps[off : off + 2], "offset": off, "total": len(comps)}
        if method == "BluTrv.GetRemoteStatus":
            trv = self.trv(params["id"])
            return {"v": 12, "status": {"sys": {"cfg_rev": 13}, "trv:0": {"schedule_rev": trv.rev}}}
        if method == "BluTrv.GetRemoteConfig":
            return {"v": 12, "config": {"trv:0": {"min_target_C": 4.0, "max_target_C": 30.0}}}
        if method == "BluTrv.Call":
            return self.trv(params["id"]).call(params["method"], params["params"])
        raise RpcError(404, f"No handler for {method}")


@pytest.fixture
def gateway():
    """Patch the RPC client with the fake gateway."""
    fake = FakeGateway()

    async def _call(self, method, params=None, timeout=0):
        return await fake.call(method, params, timeout)

    with patch("custom_components.shelly_blu_trv_schedule.rpc.GatewayClient.call", _call):
        yield fake
