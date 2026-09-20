import asyncio
import time
from dataclasses import replace

import pytest
from aiohttp import web

from grsai.client import GrsaiClient, retry_after
from grsai.errors import GrsaiError
from grsai.request_builder import build_request
from test_config_images import png

KEY = "test-secret-key"


def request(config):
    return build_request(
        "nano-banana-2", "private-prompt", {"aspectRatio": "auto", "imageSize": "1K"}, ["encoded"], config
    )


async def test_submit_once_retries_and_order(serve, caplog):
    posts, polls, auth = [], [], []
    cfg = None

    async def generate(req):
        posts.append(await req.json())
        assert req.headers["Authorization"] == f"Bearer {KEY}"
        return web.json_response({"id": "task-1", "status": "running"})

    async def result(req):
        polls.append(req.query["id"])
        if len(polls) == 1:
            return web.Response(status=503, headers={"Retry-After": "0"})
        if len(polls) == 2:
            return web.json_response({"id": "task-1", "status": "running"})
        return web.json_response(
            {
                "id": "task-1",
                "status": "succeeded",
                "results": [{"url": cfg.base_url + "/image/1"}, {"url": cfg.base_url + "/image/2"}],
            }
        )

    async def image(req):
        auth.append(req.headers.get("Authorization"))
        return web.Response(body=png((4, 3) if req.match_info["id"] == "1" else (2, 5)))

    cfg = await serve(
        [
            ("POST", "/v1/api/generate", generate),
            ("GET", "/v1/api/result", result),
            ("GET", "/image/{id}", image),
        ]
    )
    stages = []

    async def progress(stage, *_):
        stages.append(stage)

    client = GrsaiClient(cfg, KEY, progress=progress)
    images = await client.generate(request(cfg))
    assert len(posts) == 1 and polls == ["task-1"] * 3
    assert [tuple(image.shape) for image in images] == [(1, 3, 4, 3), (1, 5, 2, 3)]
    assert auth == [None, None]
    assert "reconnecting" in stages
    assert KEY not in caplog.text and "private-prompt" not in caplog.text


async def test_optional_progress_and_download_counts_are_reported(serve):
    cfg = None
    polls = 0

    async def generate(req):
        return web.json_response({"id": "task-progress", "status": "running", "progress": 12})

    async def result(req):
        nonlocal polls
        polls += 1
        if polls == 1:
            return web.json_response({"id": "task-progress", "status": "running", "progress": 65})
        return web.json_response({
            "id": "task-progress", "status": "succeeded",
            "results": [{"url": cfg.base_url + "/image/1"}, {"url": cfg.base_url + "/image/2"}],
        })

    async def image(req):
        return web.Response(body=png())

    events = []

    async def progress(stage, value, task_id, details):
        events.append((stage, value, task_id, details))

    cfg = await serve([
        ("POST", "/v1/api/generate", generate),
        ("GET", "/v1/api/result", result),
        ("GET", "/image/{id}", image),
    ])
    await GrsaiClient(cfg, KEY, progress=progress).generate(request(cfg))
    assert [(stage, value) for stage, value, _, _ in events if stage == "running"] == [
        ("running", 12), ("running", 65),
    ]
    assert [(value, details) for stage, value, _, details in events if stage == "downloading"] == [
        (0, {"completed": 0, "total": 2}),
        (50, {"completed": 1, "total": 2}),
        (100, {"completed": 2, "total": 2}),
    ]


async def test_generation_progress_never_moves_backwards(serve):
    cfg = None
    responses = iter([65, 40, None])

    async def generate(req):
        return web.json_response({"id": "task-progress", "status": "running", "progress": 65})

    async def result(req):
        value = next(responses)
        if value is None:
            return web.json_response({
                "id": "task-progress", "status": "succeeded",
                "results": [{"url": cfg.base_url + "/image"}],
            })
        return web.json_response({"id": "task-progress", "status": "running", "progress": value})

    async def image(req):
        return web.Response(body=png())

    values = []

    async def progress(stage, value, *_):
        if stage == "running":
            values.append(value)

    cfg = await serve([
        ("POST", "/v1/api/generate", generate),
        ("GET", "/v1/api/result", result),
        ("GET", "/image", image),
    ])
    await GrsaiClient(cfg, KEY, progress=progress).generate(request(cfg))
    assert values == [65, 65, 65]


@pytest.mark.parametrize("bad_progress", [None, -1, 101, float("inf"), "50", True])
async def test_missing_or_invalid_progress_is_indeterminate(serve, bad_progress):
    cfg = None

    async def generate(req):
        return web.json_response({"id": "task-progress", "status": "running", "progress": bad_progress})

    async def result(req):
        return web.json_response({
            "id": "task-progress", "status": "succeeded",
            "results": [{"url": cfg.base_url + "/image"}],
        })

    async def image(req):
        return web.Response(body=png())

    events = []

    async def progress(stage, value, *_):
        events.append((stage, value))

    cfg = await serve([
        ("POST", "/v1/api/generate", generate),
        ("GET", "/v1/api/result", result),
        ("GET", "/image", image),
    ])
    await GrsaiClient(cfg, KEY, progress=progress).generate(request(cfg))
    assert ("running", None) in events


@pytest.mark.parametrize(
    "payload,status,match",
    [
        ({"id": "task-error", "status": "failed", "error": KEY}, 200, "failed"),
        ({"id": "task-error", "status": "violation", "error": "policy"}, 200, "violation"),
        ({"id": "task-error", "status": "failed", "error": "rejected"}, 400, "HTTP 400"),
        ({"id": "task-error", "status": "succeeded", "results": []}, 200, "no images"),
        ({"id": "task-error", "status": "unknown"}, 200, "Unknown"),
        ({"status": "running"}, 200, "no task ID"),
        ({"id": "task-error", "status": []}, 200, "Unknown"),
        ({"code": 1, "msg": "error"}, 200, "Unknown"),
        (None, 200, "JSON object"),
    ],
)
async def test_terminal_protocol_failures(serve, payload, status, match):
    calls = []

    async def generate(req):
        calls.append(1)
        return web.json_response(payload, status=status)

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    with pytest.raises(GrsaiError, match=match) as error:
        await GrsaiClient(cfg, KEY).generate(request(cfg))
    assert len(calls) == 1
    assert KEY not in str(error.value)
    if payload and payload.get("id"):
        assert error.value.task_id == payload["id"]


async def test_lost_submission_no_retry(serve):
    calls = []

    async def generate(req):
        calls.append(1)
        req.transport.close()
        return web.Response()

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    with pytest.raises(GrsaiError, match="may already exist"):
        await GrsaiClient(cfg, KEY).generate(request(cfg))
    assert len(calls) == 1


async def test_partial_download_failure_is_atomic(serve):
    attempts = []
    cfg = None

    async def generate(req):
        return web.json_response(
            {
                "id": "task-download",
                "status": "succeeded",
                "results": [{"url": cfg.base_url + "/image/1"}, {"url": cfg.base_url + "/image/2"}],
            }
        )

    async def image(req):
        attempts.append(req.match_info["id"])
        return web.Response(body=png()) if req.match_info["id"] == "1" else web.Response(status=502)

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/image/{id}", image)])
    with pytest.raises(GrsaiError) as error:
        await GrsaiClient(cfg, KEY).generate(request(cfg))
    assert attempts == ["1", "2", "2", "2", "2"]
    assert error.value.task_id == "task-download" and error.value.index == 2


@pytest.mark.parametrize("stage", ["submit", "poll", "backoff", "download"])
async def test_interrupt_pending_network_and_backoff(serve, stage):
    pending = asyncio.Event()
    release = asyncio.Event()
    cfg = None
    cancelled = False

    class HostInterrupt(Exception):
        pass

    def check():
        if cancelled:
            raise HostInterrupt()

    async def generate(req):
        if stage == "submit":
            pending.set()
            await release.wait()
        if stage == "download":
            return web.json_response(
                {"id": "task-cancel", "status": "succeeded", "results": [{"url": cfg.base_url + "/image"}]}
            )
        return web.json_response({"id": "task-cancel", "status": "running"})

    async def result(req):
        pending.set()
        if stage == "backoff":
            return web.Response(status=429, headers={"Retry-After": "600"})
        await release.wait()
        return web.Response(status=503)

    async def image(req):
        pending.set()
        await release.wait()
        return web.Response(body=png())

    cfg = await serve(
        [("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result), ("GET", "/image", image)]
    )
    task = asyncio.create_task(GrsaiClient(cfg, KEY, check).generate(request(cfg)))
    await asyncio.wait_for(pending.wait(), 2)
    if stage == "backoff":
        await asyncio.sleep(0.03)
    started = time.monotonic()
    cancelled = True
    with pytest.raises(HostInterrupt):
        await task
    assert time.monotonic() - started < 1
    release.set()


async def test_optional_total_deadline(serve):
    async def running(req):
        return web.json_response({"id": "task-timeout", "status": "running"})

    cfg = await serve([("POST", "/v1/api/generate", running), ("GET", "/v1/api/result", running)])
    cfg = replace(cfg, transport=replace(cfg.transport, task_timeout_seconds=0.04))
    with pytest.raises(GrsaiError, match="deadline") as error:
        await GrsaiClient(cfg, KEY).generate(request(cfg))
    assert error.value.task_id == "task-timeout"


def test_retry_after():
    assert retry_after("3600") == 3600
    assert retry_after("invalid") is None
    assert retry_after("inf") is None
    assert retry_after("Wed, 01 Jan 2020 00:00:00 GMT") == 0


async def test_no_legacy_deadline_with_advanced_poll_clock(serve):
    elapsed, polls = 0, 0
    cfg = None

    async def generate(req):
        return web.json_response({"id": "long-task", "status": "running"})

    async def result(req):
        nonlocal polls
        polls += 1
        if elapsed < 900:
            return web.json_response({"id": "long-task", "status": "running"})
        return web.json_response(
            {"id": "long-task", "status": "succeeded", "results": [{"url": cfg.base_url + "/image"}]}
        )

    async def image(req):
        return web.Response(body=png())

    cfg = await serve(
        [("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result), ("GET", "/image", image)]
    )
    cfg = replace(cfg, transport=replace(cfg.transport, poll_interval_seconds=3))

    class ClockClient(GrsaiClient):
        async def _wait(self, seconds):
            nonlocal elapsed
            elapsed += seconds
            await asyncio.sleep(0)

    assert cfg.transport.task_timeout_seconds is None
    assert len(await ClockClient(cfg, KEY).generate(request(cfg))) == 1
    assert elapsed == 900 and polls == 300
