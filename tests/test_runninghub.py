from dataclasses import replace

import pytest
from aiohttp import web

from grsai.api_settings import build_provider_config
from grsai.errors import GrsaiError
from grsai.images import validate_reference_files
from grsai.request_builder import build_runninghub_request
from grsai.runninghub_client import RunningHubClient
from grsai.runninghub_config import get_runninghub_catalog
from test_config_images import png

KEY = "runninghub-test-key"


def test_curated_runninghub_catalog_and_payloads():
    catalog = get_runninghub_catalog()
    assert catalog.base_url == "https://www.runninghub.ai"
    assert catalog.default_model == "rh:nano-banana-2-stable"
    assert len(catalog.models) == 8
    assert all(model.startswith("rh:") for model in catalog.models)
    assert all(profile.max_reference_images == 10 for profile in catalog.models.values())

    stable = "rh:gpt-image-2.5-sunburst-stable"
    profile = catalog.profile(stable)
    params = {name: rule.default for name, rule in profile.parameters.items()}
    payload = build_runninghub_request(stable, "edit", params, ["https://example.test/input.png"], catalog)
    assert payload == {
        "prompt": "edit",
        "imageUrls": ["https://example.test/input.png"],
        "aspectRatio": "16:9",
        "resolution": "2k",
        "quality": "high",
        "background": "opaque",
        "outputFormat": "png",
    }

    nano = "rh:nano-banana-pro-economy"
    profile = catalog.profile(nano)
    params = {name: rule.default for name, rule in profile.parameters.items()}
    payload = build_runninghub_request(nano, "edit", params, ["url"], catalog)
    assert "aspectRatio" not in payload
    assert payload["resolution"] == "1k"
    assert "model" not in payload


def test_provider_config_defaults_and_token_scope(config):
    catalog = get_runninghub_catalog()
    settings = build_provider_config(" key ", "", "grsai-token", "runninghub", config.base_url, catalog.base_url)
    assert settings.provider == "runninghub"
    assert settings.base_url == catalog.base_url
    assert settings.token is None
    with pytest.raises(ValueError, match="Provider"):
        build_provider_config("key", "", "", "other", config.base_url, catalog.base_url)


def test_shared_reference_limits():
    validate_reference_files([b"x"] * 10)
    with pytest.raises(ValueError, match="1 to 10"):
        validate_reference_files([])
    with pytest.raises(ValueError, match="1 to 10"):
        validate_reference_files([b"x"] * 11)
    with pytest.raises(ValueError, match="10 MB"):
        validate_reference_files([b"x" * 10_000_001])
    nine_mb = b"x" * 9_000_000
    with pytest.raises(ValueError, match="50 MB"):
        validate_reference_files([nine_mb] * 6)


async def test_runninghub_upload_submit_poll_and_download(serve):
    uploads, submissions, queries, download_auth = [], [], [], []
    cfg = None
    endpoint = "/openapi/v2/rhart-image-n-pro/edit"

    async def upload(req):
        assert req.headers["Authorization"] == f"Bearer {KEY}"
        reader = await req.multipart()
        field = await reader.next()
        uploads.append(await field.read())
        return web.json_response({
            "code": 0,
            "message": "success",
            "data": {"download_url": cfg.base_url + "/temporary/input.png"},
        })

    async def submit(req):
        submissions.append(await req.json())
        return web.json_response({"taskId": "rh-task-1", "status": "RUNNING", "errorCode": "", "errorMessage": ""})

    async def query(req):
        queries.append((await req.json())["taskId"])
        if len(queries) == 1:
            return web.json_response({"taskId": "rh-task-1", "status": "RUNNING", "errorCode": "", "errorMessage": ""})
        return web.json_response({
            "taskId": "rh-task-1",
            "status": "SUCCESS",
            "errorCode": "",
            "errorMessage": "",
            "results": [{"url": cfg.base_url + "/result.png", "outputType": "png"}],
        })

    async def result(req):
        download_auth.append(req.headers.get("Authorization"))
        return web.Response(body=png())

    cfg = await serve([
        ("POST", "/openapi/v2/media/upload/binary", upload),
        ("POST", endpoint, submit),
        ("POST", "/openapi/v2/query", query),
        ("GET", "/result.png", result),
    ])
    client = RunningHubClient(cfg, KEY, endpoint=endpoint)
    urls = await client.upload_images([png()])
    request = build_runninghub_request(
        "rh:nano-banana-pro-economy",
        "private prompt",
        {"aspectRatio": "auto", "resolution": "1k"},
        urls,
        get_runninghub_catalog(),
    )
    images = await client.generate(request)
    assert len(uploads) == 1 and uploads[0].startswith(b"\x89PNG")
    assert submissions == [{"prompt": "private prompt", "imageUrls": urls, "resolution": "1k"}]
    assert queries == ["rh-task-1", "rh-task-1"]
    assert len(images) == 1 and download_auth == [None]
    assert client.task_id == "rh-task-1" and client.remote_status == "SUCCESS"


async def test_runninghub_lost_submission_is_not_retried(serve):
    calls = []
    endpoint = "/openapi/v2/rhart-image-n-pro/edit"

    async def submit(req):
        calls.append(1)
        req.transport.close()
        return web.Response()

    cfg = await serve([("POST", endpoint, submit)])
    client = RunningHubClient(cfg, KEY, endpoint=endpoint)
    with pytest.raises(GrsaiError, match="may already exist"):
        await client.generate({"prompt": "edit", "imageUrls": ["url"], "resolution": "1k"})
    assert calls == [1]


async def test_runninghub_total_deadline(serve):
    endpoint = "/openapi/v2/rhart-image-n-pro/edit"

    async def running(req):
        return web.json_response({"taskId": "rh-timeout", "status": "RUNNING", "errorCode": "", "errorMessage": ""})

    cfg = await serve([("POST", endpoint, running), ("POST", "/openapi/v2/query", running)])
    cfg = replace(cfg, transport=replace(cfg.transport, task_timeout_seconds=0.03))
    with pytest.raises(GrsaiError, match="deadline"):
        await RunningHubClient(cfg, KEY, endpoint=endpoint).generate({
            "prompt": "edit", "imageUrls": ["url"], "resolution": "1k"
        })


async def test_runninghub_terminal_error_is_not_retried_as_transient(serve):
    endpoint = "/openapi/v2/rhart-image-n-pro/edit"
    queries = []

    async def submit(req):
        return web.json_response({
            "taskId": "rh-failed", "status": "RUNNING", "errorCode": "", "errorMessage": ""
        })

    async def query(req):
        queries.append(1)
        return web.json_response(
            {
                "taskId": "rh-failed",
                "status": "FAILED",
                "errorCode": "UPSTREAM_FAILED",
                "errorMessage": "model rejected input",
            },
            status=503,
        )

    cfg = await serve([("POST", endpoint, submit), ("POST", "/openapi/v2/query", query)])
    client = RunningHubClient(cfg, KEY, endpoint=endpoint)
    with pytest.raises(GrsaiError, match="model rejected input"):
        await client.generate({"prompt": "edit", "imageUrls": ["url"], "resolution": "1k"})
    assert queries == [1]
    assert client.task_id == "rh-failed"
    assert client.remote_status == "FAILED"


async def test_runninghub_upload_exposes_auth_failure_status(serve):
    async def upload(req):
        return web.json_response({"code": 401, "message": "invalid key"}, status=401)

    cfg = await serve([("POST", "/openapi/v2/media/upload/binary", upload)])
    client = RunningHubClient(cfg, KEY, endpoint="/openapi/v2/rhart-image-n-pro/edit")
    with pytest.raises(GrsaiError, match="invalid key"):
        await client.upload_images([png()])
    assert client.http_status == 401
