import asyncio

import pytest
from aiohttp import web

from grsai.balance import BalanceManager, parse_credits, query_balance
from grsai.errors import GrsaiError


@pytest.mark.parametrize("credits", [0, 10_000, 1.25])
def test_credits(credits):
    assert parse_credits({"code": 0, "data": {"credits": credits}}, "key") == credits


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"code": False, "data": {"credits": 0}},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"credits": "0"}},
        {"code": 0, "data": {"credits": True}},
        {"code": 0, "data": {"credits": float("nan")}},
        {"code": 1, "msg": "secret"},
    ],
)
def test_invalid_balance_not_zero(payload):
    with pytest.raises(GrsaiError) as error:
        parse_credits(payload, "secret")
    assert "secret" not in str(error.value)


async def test_balance_http_contract(serve):
    async def balance(req):
        assert "Authorization" not in req.headers
        assert await req.json() == {"token": "token"}
        return web.json_response({"code": 0, "data": {"credits": 0}})

    cfg = await serve([("POST", "/client/openapi/getCredits", balance)])
    assert await query_balance(cfg.base_url, "token") == 0


async def test_manager_latest_and_cleanup(monkeypatch):
    from grsai import balance

    events = []

    async def query(base_url, token, check):
        await asyncio.sleep(0.03 if token == "old" else 0.001)
        return 7

    monkeypatch.setattr(balance, "query_balance", query)
    manager = BalanceManager(lambda payload, sid: events.append(payload))
    manager.start("1", "client", "host", "old", 1)
    await asyncio.sleep(0)
    manager.start("1", "client", "host", "new", 2)
    await asyncio.sleep(0.05)
    assert [(e["sequence"], e["credits"]) for e in events if e["state"] == "ready"] == [(2, 7)]
    await manager.close()
    assert not manager.tasks
    manager.start("1", "client", "host", "old", 3)
    assert not manager.tasks


async def test_manager_interrupted_does_not_query(monkeypatch):
    events = []
    manager = BalanceManager(lambda payload, sid: events.append(payload), lambda: True)
    manager.start("1", "client", "host", "key", 1)
    assert not manager.tasks and events[-1]["state"] == "stale"
