"""Tests for the gateway RPC client."""

from __future__ import annotations

import hashlib
import json

import pytest
from aiohttp import web

from custom_components.shelly_blu_trv_control.rpc import (
    GatewayAuthError,
    GatewayClient,
    RpcError,
)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _app(password: str | None, trv_result=None):
    async def rpc(request: web.Request) -> web.Response:
        body = await request.json()
        if password is not None:
            auth = body.get("auth")
            ok = False
            if auth:
                ha1 = _sha(f"admin:gw:{password}")
                ha2 = _sha("dummy_method:dummy_uri")
                ok = auth["response"] == _sha(
                    f"{ha1}:{auth['nonce']}:1:{auth['cnonce']}:auth:{ha2}"
                )
            if not ok:
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": 'Digest qop="auth", realm="gw", nonce="1700000000", algorithm=SHA-256'
                    },
                )
        if body["method"] == "BluTrv.Call":
            result = trv_result
        else:
            result = {"echo": body["method"]}
        if body["method"] == "Nope":
            return web.json_response(
                {"id": body["id"], "error": {"code": 404, "message": "No handler"}}
            )
        return web.Response(text=json.dumps({"id": body["id"], "result": result}))

    app = web.Application()
    app.router.add_post("/rpc", rpc)
    return app


async def _client(aiohttp_client, app, password=None):
    server = await aiohttp_client(app)
    return GatewayClient(server.session, server.host, server.port, password)


async def test_digest_auth(hass, aiohttp_client, socket_enabled) -> None:
    client = await _client(aiohttp_client, _app("secret"), "secret")
    assert await client.call("Shelly.GetDeviceInfo") == {"echo": "Shelly.GetDeviceInfo"}
    # Nonce is reused for the following call
    assert await client.call("Shelly.GetStatus") == {"echo": "Shelly.GetStatus"}


async def test_wrong_password(hass, aiohttp_client, socket_enabled) -> None:
    client = await _client(aiohttp_client, _app("secret"), "wrong")
    with pytest.raises(GatewayAuthError):
        await client.call("Shelly.GetDeviceInfo")
    client = await _client(aiohttp_client, _app("secret"))
    with pytest.raises(GatewayAuthError):
        await client.call("Shelly.GetDeviceInfo")


async def test_rpc_errors(hass, aiohttp_client, socket_enabled) -> None:
    client = await _client(aiohttp_client, _app(None))
    with pytest.raises(RpcError):
        await client.call("Nope")

    client = await _client(
        aiohttp_client, _app(None, {"code": -103, "message": "Argument `target_C` is invalid"})
    )
    with pytest.raises(RpcError) as err:
        await client.trv_call(200, "TRV.AddScheduleRule", {"id": 0, "rule": {}})
    assert err.value.code == -103

    client = await _client(aiohttp_client, _app(None, {"rule_id": 3}))
    assert await client.trv_call(200, "TRV.AddScheduleRule", {"id": 0}) == {"rule_id": 3}
