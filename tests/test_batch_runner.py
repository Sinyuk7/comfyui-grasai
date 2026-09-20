import asyncio
import json
import time
from dataclasses import replace

import pytest
import torch
from aiohttp import web
from PIL import Image

from grsai.batch_plan import plan_batch
from grsai.batch_runner import BatchRunner
from grsai.batch_storage import BatchStore, StorageError
from grsai.client import GrsaiClient
from test_config_images import png

KEY = "batch-secret-key"
PARAMETERS = {"aspectRatio": "auto", "imageSize": "1K"}


def plan(n=2, prompts=None):
    return plan_batch({"reference_1": [torch.zeros(1, 2, 3, 3)],
                       "reference_2": [torch.ones(n, 3, 2, 3)]},
                      ["fallback-private"], prompts)


def runner(cfg, tmp_path, p=None, **kwargs):
    return BatchRunner(cfg, KEY, p or plan(), "nano-banana-2", PARAMETERS, 2, "Clothes", tmp_path, **kwargs)


async def test_variants_concurrency_order_id_persistence_and_immediate_save(serve, tmp_path):
    cfg, batch = None, None
    posts, auth, saved_order = [], [], []
    active, peak = 0, 0

    async def generate(req):
        nonlocal active, peak
        payload = await req.json()
        posts.append(payload)
        index = len(posts)
        active += 1
        peak = max(peak, active)
        return web.json_response({"id": f"task-{index}", "status": "running"})

    async def result(req):
        index = int(req.query["id"].split("-")[1])
        manifest = json.loads(batch.store.manifest.read_text())
        assert manifest["tasks"][index - 1]["remote_task_id"] == req.query["id"]
        await asyncio.sleep(0.04 if index == 1 else 0.005)
        return web.json_response({"id": req.query["id"], "status": "succeeded",
                                  "results": [{"url": cfg.base_url + f"/image/{index}"}]})

    async def image(req):
        nonlocal active
        auth.append(req.headers.get("Authorization"))
        active -= 1
        return web.Response(body=png((int(req.match_info["index"]) + 1, 3)))

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result),
                       ("GET", "/image/{index}", image)])
    stages = []

    async def progress(payload):
        stages.append(payload)

    batch = runner(cfg, tmp_path, plan(2, ["pose-1-private", "pose-2-private"]), progress=progress)
    save = batch.store.save

    async def record_save(index, *args):
        await save(index, *args)
        assert (batch.store.path / f"Clothes_{index:03d}.png").exists()
        saved_order.append(index)

    batch.store.save = record_save
    images, path = await batch.run()
    assert peak == 2 and len(posts) == 4 and saved_order[0] == 2
    assert [p["prompt"] for p in posts] == ["pose-1-private", "pose-2-private"] * 2
    assert all(len(p["images"]) == 2 for p in posts)
    assert [i.shape[2] for i in images] == [2, 3, 4, 5]
    assert auth == [None] * 4
    manifest = json.loads(batch.store.manifest.read_text())
    assert str(batch.store.manifest) == path and manifest["status"] == "succeeded"
    assert [(t["base_index"], t["prompt_index"]) for t in manifest["tasks"]] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert [t["outputs"][0]["flat_output_index"] for t in manifest["tasks"]] == list(range(4))
    text = batch.store.manifest.read_text()
    assert KEY not in text and "pose-1-private" not in text and "base64" not in text
    assert stages[0]["stage"] == "planned" and stages[0]["total"] == 4
    assert stages[-1]["completed"] == 4
    assert not batch.encoded
    for path in batch.store.path.glob("*.png"):
        with Image.open(path) as img:
            assert not img.info


async def test_partial_and_failed_results_keep_indices(serve, tmp_path):
    cfg = None
    calls = []

    async def generate(req):
        calls.append(1)
        index = len(calls)
        if index == 2:
            return web.json_response({"id": "failed-2", "status": "failed", "error": KEY})
        urls = [{"url": cfg.base_url + "/good"}]
        if index == 1:
            urls.append({"url": cfg.base_url + "/bad"})
        return web.json_response({"id": f"task-{index}", "status": "succeeded", "results": urls})

    async def good(req):
        return web.Response(body=png())

    async def bad(req):
        return web.Response(status=502)

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/good", good), ("GET", "/bad", bad)])
    cfg = replace(cfg, transport=replace(cfg.transport, download_retry_limit=0))
    batch = runner(cfg, tmp_path, plan(3))
    images, _ = await batch.run()
    assert len(calls) == 3 and len(images) == 2
    data = json.loads(batch.store.manifest.read_text())
    assert data["status"] == "partial"
    assert [t["status"] for t in data["tasks"]] == ["partial", "failed", "succeeded"]
    assert sorted(p.name for p in batch.store.path.glob("*.png")) == ["Clothes_001_01.png", "Clothes_003.png"]
    assert data["tasks"][2]["outputs"][0]["flat_output_index"] == 1
    assert KEY not in batch.store.manifest.read_text()


async def test_all_failed_returns_empty_images_and_manifest(serve, tmp_path):
    async def generate(req):
        return web.json_response({"id": "failed", "status": "violation", "error": "blocked"})

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    batch = runner(cfg, tmp_path)
    images, path = await batch.run()
    assert images == [] and path.endswith("manifest.json")
    assert batch.store.state["status"] == "failed"


@pytest.mark.parametrize("http_status", [401, 403])
async def test_fatal_auth_stops_pending(serve, tmp_path, http_status):
    calls = []

    async def generate(req):
        calls.append(1)
        return web.json_response({"error": "disabled"}, status=http_status)

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    batch = runner(cfg, tmp_path, plan(10))
    await batch.run()
    assert len(calls) <= 2
    assert sum(t["status"] == "pending" for t in batch.store.state["tasks"]) >= 8


async def test_lost_post_not_retried(serve, tmp_path):
    calls = []

    async def generate(req):
        calls.append(1)
        req.transport.close()
        return web.Response()

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    batch = runner(cfg, tmp_path, plan(1))
    await batch.run()
    assert len(calls) == 1
    assert batch.store.state["tasks"][0]["status"] == "submission_unknown"


async def test_retry_after_pauses_new_tasks_without_retrying_post(serve, tmp_path):
    times = []

    async def generate(req):
        times.append(time.monotonic())
        return web.json_response({"error": "busy"}, status=429, headers={"Retry-After": "0.12"})

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    batch = runner(cfg, tmp_path, plan(4))
    await batch.run()
    assert len(times) == 4
    assert times[2] - times[0] >= 0.1


async def test_cancel_waiting_tasks_and_persist_known_ids(serve, tmp_path):
    polled = asyncio.Event()
    calls = []

    async def generate(req):
        calls.append(1)
        return web.json_response({"id": f"id-{len(calls)}", "status": "running"})

    async def result(req):
        polled.set()
        return web.Response(status=429, headers={"Retry-After": "600"})

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result)])
    batch = runner(cfg, tmp_path, plan(10))
    task = asyncio.create_task(batch.run())
    await asyncio.wait_for(polled.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    data = json.loads(batch.store.manifest.read_text())
    assert data["status"] == "interrupted" and len(calls) <= 2
    assert data["tasks"][0]["remote_task_id"]
    assert data["tasks"][0]["remote_status"] == "running"
    assert all(t["status"] == "pending" for t in data["tasks"][2:])


async def test_id_storage_failure_stops_polling_and_new_submissions(serve, tmp_path, monkeypatch):
    calls, polls = [], []

    async def generate(req):
        calls.append(1)
        return web.json_response({"id": f"id-{len(calls)}", "status": "running"})

    async def result(req):
        polls.append(1)
        return web.Response(status=500)

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result)])
    batch = runner(cfg, tmp_path, plan(10))
    write = batch.store._write

    def failure():
        if any(t["remote_task_id"] for t in batch.store.state["tasks"]):
            raise StorageError("disk full")
        write()

    monkeypatch.setattr(batch.store, "_write", failure)
    with pytest.raises(StorageError):
        await batch.run()
    assert len(calls) <= 2 and not polls
    assert any(t["remote_task_id"] for t in batch.store.state["tasks"])


async def test_manifest_single_writer_and_unique_batch_paths(config, tmp_path):
    first = runner(config, tmp_path, plan(10))
    second = runner(config, tmp_path, plan(10))
    assert first.store.path != second.store.path
    await asyncio.gather(*(first.store.update(i, remote_task_id=f"id-{i}") for i in range(1, 11)))
    data = json.loads(first.store.manifest.read_text())
    assert [t["remote_task_id"] for t in data["tasks"]] == [f"id-{i}" for i in range(1, 11)]
    assert not list(first.store.path.glob(".grsai-*"))


async def test_client_id_callback_is_awaited_before_polling(serve):
    accepted = asyncio.Event()
    release = asyncio.Event()
    polls = []

    async def generate(req):
        return web.json_response({"id": "id-1", "status": "running"})

    async def result(req):
        polls.append(1)
        return web.json_response({"id": "id-1", "status": "failed", "error": "finished"})

    async def receive(*_):
        accepted.set()
        await release.wait()

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result)])
    client = GrsaiClient(cfg, KEY, accepted=receive)
    task = asyncio.create_task(client.generate({"prompt": "private", "images": []}))
    await asyncio.wait_for(accepted.wait(), 2)
    await asyncio.sleep(0.02)
    assert not polls
    release.set()
    with pytest.raises(RuntimeError, match="finished"):
        await task
    assert polls == [1]


def test_bad_parameters_preflight_before_creating_store(config, tmp_path):
    with pytest.raises(ValueError):
        BatchRunner(config, KEY, plan(), "nano-banana-2", {}, 2, "Clothes", tmp_path)
    assert not list(tmp_path.iterdir())


async def test_manifest_redacts_key_in_source_metadata(config, tmp_path):
    p = plan(1)
    col = p.columns[0]
    source = replace(col.sources[0], filename=f"{KEY}.png", path=f"/tmp/{KEY}.png")
    p = replace(p, columns=(replace(col, sources=(source,)), *p.columns[1:]))
    store = BatchStore(tmp_path, p, "nano-banana-2", PARAMETERS, 2, "Clothes", KEY)
    assert KEY not in store.manifest.read_text()


async def test_base_encoding_is_reused_across_variants(config, tmp_path, monkeypatch):
    from grsai import batch_runner

    encoded = []
    encode = batch_runner.encode_images

    def count(*args):
        encoded.append(1)
        return encode(*args)

    async def generate(self, request):
        self.submitted = True
        await asyncio.sleep(0)
        image = torch.zeros(1, 2, 3, 3)
        await self.result(image, 1, 1)
        return [image]

    monkeypatch.setattr(batch_runner, "encode_images", count)
    monkeypatch.setattr(GrsaiClient, "generate", generate)
    batch = runner(config, tmp_path, plan(3, ["one", "two", "three", "four"]))
    images, _ = await batch.run()
    assert len(images) == 12 and len(encoded) == 4  # One shared image, three varying images.


async def test_progress_failure_is_nonfatal(config, tmp_path, monkeypatch):
    async def generate(self, request):
        self.submitted = True
        image = torch.zeros(1, 2, 3, 3)
        await self.result(image, 1, 1)
        return [image]

    async def display(_):
        raise RuntimeError("UI is gone")

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    batch = runner(config, tmp_path, plan(1), progress=display)
    assert len((await batch.run())[0]) == 1


async def test_child_progress_is_forwarded_with_batch_context(config, tmp_path, monkeypatch):
    async def generate(self, request):
        self.submitted = True
        self.task_id = "remote-1"
        await self.progress("submitting", None, None, {})
        await self.progress("running", 42, self.task_id, {})
        await self.progress("downloading", 50, self.task_id, {"completed": 1, "total": 2})
        await self.result(torch.zeros(1, 2, 3, 3), 1, 1)
        self.remote_status = "succeeded"
        return []

    events = []

    async def display(payload):
        events.append(payload)

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    batch = runner(config, tmp_path, plan(1), progress=display)
    await batch.run()
    live = [event for event in events if event["stage"] == "running" and event["active"]]
    assert [event["active"][0]["stage"] for event in live] == [
        "submitting", "running", "downloading",
    ]
    assert live[1]["active"][0]["progress"] == 42
    assert live[2]["active"][0]["completed"] == 1
    assert events[-1]["completed"] == 1 and events[-1]["active"] == []


async def test_storage_failure_retains_saved_results_and_stops_pending(config, tmp_path, monkeypatch):
    calls = []

    async def generate(self, request):
        calls.append(1)
        self.submitted = True
        self.task_id = f"id-{len(calls)}"
        await self.accepted(self.task_id, "succeeded")
        await self.result(torch.zeros(1, 2, 3, 3), 1, 1)
        return []

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    batch = runner(config, tmp_path, plan(10))
    original = batch.store.save

    async def save(index, *args):
        if index > 1:
            raise StorageError("disk full")
        await original(index, *args)

    batch.store.save = save
    with pytest.raises(StorageError):
        await batch.run()
    assert (batch.store.path / "Clothes_001.png").exists()
    assert len(calls) <= 3
    data = json.loads(batch.store.manifest.read_text())
    assert data["status"] == "failed" and data["summary"]["saved_images"] == 1
    assert data["tasks"][1]["remote_task_id"] == "id-2"
    assert data["tasks"][0]["outputs"][0]["flat_output_index"] is None
