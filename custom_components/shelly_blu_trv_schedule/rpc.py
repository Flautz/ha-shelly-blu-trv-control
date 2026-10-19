"""Minimal JSON-RPC client for a Shelly BLU Gateway Gen3."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import re
import secrets
from typing import Any

import aiohttp

from .const import LOCAL_RPC_TIMEOUT, RPC_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_AUTH_FIELD_RE = re.compile(r'(\w+)="?([^",]*)"?')


class GatewayError(Exception):
    """Base error talking to the gateway."""


class GatewayConnectionError(GatewayError):
    """The gateway could not be reached."""


class GatewayAuthError(GatewayError):
    """The gateway rejected the credentials."""


class RpcError(GatewayError):
    """The gateway or the TRV returned an RPC error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class GatewayClient:
    """Talks JSON-RPC over HTTP to a BLU Gateway and relays calls to TRVs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        password: str | None = None,
        username: str = "admin",
    ) -> None:
        self._session = session
        self._url = f"http://{host}:{port}/rpc"
        self._username = username
        self._password = password or None
        self._ids = itertools.count(1)
        self._auth_params: dict[str, str] | None = None
        # The gateway relays BLE calls one at a time; serialize them so
        # concurrent UI/service calls do not time out each other.
        self.ble_lock = asyncio.Lock()

    @property
    def host_url(self) -> str:
        """Return the RPC endpoint (for diagnostics)."""
        return self._url

    def _auth(self) -> dict[str, Any] | None:
        if not self._auth_params or not self._password:
            return None
        realm = self._auth_params["realm"]
        nonce = self._auth_params["nonce"]
        cnonce = secrets.randbelow(2**31)
        ha1 = _sha256(f"{self._username}:{realm}:{self._password}")
        ha2 = _sha256("dummy_method:dummy_uri")
        response = _sha256(f"{ha1}:{nonce}:1:{cnonce}:auth:{ha2}")
        return {
            "realm": realm,
            "username": self._username,
            "nonce": int(nonce) if nonce.isdigit() else nonce,
            "cnonce": cnonce,
            "response": response,
            "algorithm": "SHA-256",
        }

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = LOCAL_RPC_TIMEOUT
    ) -> Any:
        """Call an RPC method on the gateway and return its result."""
        for attempt in range(2):
            payload: dict[str, Any] = {"id": next(self._ids), "method": method}
            if params is not None:
                payload["params"] = params
            if (auth := self._auth()) is not None:
                payload["auth"] = auth
            try:
                async with self._session.post(
                    self._url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 401:
                        header = resp.headers.get("WWW-Authenticate", "")
                        fields = dict(_AUTH_FIELD_RE.findall(header))
                        if attempt or not self._password or "nonce" not in fields:
                            raise GatewayAuthError("Authentication failed")
                        self._auth_params = fields
                        continue
                    data = await resp.json(content_type=None)
            except TimeoutError as err:
                raise GatewayConnectionError(f"Timeout calling {method}") from err
            except aiohttp.ClientError as err:
                raise GatewayConnectionError(f"Error calling {method}: {err}") from err
            if not isinstance(data, dict):
                raise GatewayError(f"Unexpected response to {method}: {data!r}")
            if (error := data.get("error")) is not None:
                if error.get("code") == 401:
                    raise GatewayAuthError(error.get("message", "Unauthorized"))
                raise RpcError(error.get("code", -1), error.get("message", str(error)))
            return data.get("result")
        raise GatewayAuthError("Authentication failed")

    async def trv_call(self, trv_id: int, method: str, params: dict[str, Any] | None = None) -> Any:
        """Relay an RPC call to a BLU TRV over Bluetooth."""
        async with self.ble_lock:
            _LOGGER.debug("BluTrv.Call %s %s %s", trv_id, method, params)
            result = await self.call(
                "BluTrv.Call",
                {"id": trv_id, "method": method, "params": params or {"id": 0}},
                timeout=RPC_TIMEOUT,
            )
        # Errors of the TRV are returned as the *result* of BluTrv.Call.
        if (
            isinstance(result, dict)
            and "code" in result
            and "message" in result
            and isinstance(result["code"], int)
            and result["code"] < 0
        ):
            raise RpcError(result["code"], result["message"])
        return result

    async def get_components(self) -> list[dict[str, Any]]:
        """Return all gateway components (handles pagination)."""
        components: list[dict[str, Any]] = []
        offset = 0
        while True:
            result = await self.call("Shelly.GetComponents", {"offset": offset})
            batch = result.get("components", [])
            components.extend(batch)
            offset += len(batch)
            if not batch or offset >= result.get("total", 0):
                return components
