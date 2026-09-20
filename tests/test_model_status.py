import pytest
from aiohttp import web

from grsai.errors import GrsaiError
from grsai.model_status import parse_model_status, query_model_status


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"code": 0, "data": {"status": True, "error": ""}}, (True, "")),
        ({"code": 0, "data": {"status": False, "error": "Service unavailable"}},
         (False, "Service unavailable")),
    ],
)
def test_parse_model_status(payload, expected):
    assert parse_model_status(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"code": False, "data": {"status": True, "error": ""}},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"status": 1, "error": ""}},
        {"code": 0, "data": {"status": False, "error": None}},
        {"code": 1, "msg": "failed"},
    ],
)
def test_parse_model_status_rejects_invalid_payloads(payload):
    with pytest.raises(GrsaiError):
        parse_model_status(payload)


async def test_model_status_http_contract(serve):
    async def status(req):
        assert dict(req.query) == {"model": "model/name"}
        assert "Authorization" not in req.headers
        return web.json_response({"code": 0, "data": {"status": False, "error": "Paused"}})

    cfg = await serve([("GET", "/client/common/getModelStatus", status)])
    assert await query_model_status(cfg.base_url, "model/name") == (False, "Paused")
