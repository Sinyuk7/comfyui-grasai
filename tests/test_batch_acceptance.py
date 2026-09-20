"""Targeted acceptance regressions using local HTTP and controlled scheduling."""

import asyncio
import json
import time
from pathlib import Path

import pytest
import torch
from aiohttp import web

from grsai.batch_plan import plan_batch
from grsai.batch_runner import BatchRunner
from grsai.batch_storage import BatchStore
from grsai.references import load_folder
from test_config_images import png

KEY = "acceptance-fake-key"
PARAMETERS = {"aspectRatio": "auto", "imageSize": "1K"}


def make_plan():
    return plan_batch({"reference_1": [torch.zeros(1, 2, 3, 3)],
                       "reference_2": [torch.zeros(10, 2, 3, 3)]},
                      ["ignored"], ["standing", "sitting", "running", "lying"])


@pytest.mark.parametrize("blocked_stage", ["download", "save"])
async def test_forty_tasks_hold_four_slots_until_saved(serve, tmp_path, blocked_stage):
    cfg = None
    posts, saved, active, snapshots = [], [], set(), []
    gates = {i: asyncio.Event() for i in range(1, 5)}
    reached = {i: asyncio.Event() for i in range(1, 5)}
    fifth = asyncio.Event()
    peak = 0

    async def generate(req):
        nonlocal peak
        payload = await req.json()
        index = len(posts) + 1
        posts.append(payload)
        active.add(index)
        peak = max(peak, len(active))
        snapshots.append((index, len(active), tuple(saved)))
        if index == 5:
            fifth.set()
        return web.json_response({"id": f"task-{index}", "status": "succeeded",
                                  "results": [{"url": cfg.base_url + f"/image/{index}"}]})

    async def image(req):
        index = int(req.match_info["index"])
        if blocked_stage == "download" and index <= 4:
            reached[index].set()
            await gates[index].wait()
        return web.Response(body=png((index + 1, 2)))

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/image/{index}", image)])
    batch = BatchRunner(cfg, KEY, make_plan(), "nano-banana-2", PARAMETERS, 4, "Clothes", tmp_path)
    original_save = batch.store.save

    async def save(index, *args):
        if blocked_stage == "save" and index <= 4:
            reached[index].set()
            await gates[index].wait()
        await original_save(index, *args)
        assert (batch.store.path / f"Clothes_{index:03d}.png").exists()
        active.remove(index)
        saved.append(index)

    batch.store.save = save
    run = asyncio.create_task(batch.run())
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in reached.values())), 3)
        assert len(posts) == 4 and active == {1, 2, 3, 4} and not saved
        # A negative bounded observation: all four gates remain closed throughout.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fifth.wait(), 0.05)
        gates[3].set()
        await asyncio.wait_for(fifth.wait(), 3)
        assert saved[0] == 3 and snapshots[4][2] == (3,)
        assert not gates[1].is_set() and not gates[2].is_set()
        for gate in gates.values():
            gate.set()
        images, manifest = await asyncio.wait_for(run, 10)
    finally:
        for gate in gates.values():
            gate.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)

    assert len(posts) == len(images) == len(saved) == 40 and peak == 4 and not active
    assert all(inflight <= 4 for _, inflight, _ in snapshots)
    assert [p["prompt"] for p in posts] == list(make_plan().prompts) * 10
    assert [i.shape[2] for i in images] == list(range(2, 42))
    data = json.loads(Path(manifest).read_text())
    assert data["total_tasks"] == 40 and data["max_concurrency"] == 4
    for index, task in enumerate(data["tasks"], 1):
        assert (task["base_index"], task["prompt_index"]) == ((index - 1) // 4 + 1, (index - 1) % 4 + 1)
        assert task["remote_task_id"] == f"task-{index}"
        assert task["outputs"] == [{"result_index": 1, "file": f"Clothes_{index:03d}.png",
                                    "flat_output_index": index - 1}]


async def test_slow_manifest_replaces_preserve_monotonic_task_state(tmp_path, monkeypatch):
    from grsai import batch_storage

    store = BatchStore(tmp_path, make_plan(), "nano-banana-2", PARAMETERS, 4, "Clothes", KEY)
    real_replace = batch_storage.os.replace
    snapshots = []
    writers, peak = 0, 0

    def slow_replace(source, destination):
        nonlocal writers, peak
        if Path(destination) != store.manifest:
            return real_replace(source, destination)
        assert store.lock.locked()
        previous = json.loads(store.manifest.read_text())
        snapshot = json.loads(Path(source).read_text())
        writers += 1
        peak = max(peak, writers)
        try:
            time.sleep(0.001)  # Exercise the actual synchronous atomic-write boundary.
            assert json.loads(store.manifest.read_text()) == previous
            real_replace(source, destination)
            assert json.loads(store.manifest.read_text()) == snapshot
            snapshots.append(snapshot)
        finally:
            writers -= 1

    monkeypatch.setattr(batch_storage.os, "replace", slow_replace)

    async def producer(index):
        await store.update(index, status="running", remote_task_id=f"id-{index}")
        await asyncio.sleep(0)
        await store.save(index, torch.zeros(1, 2, 3, 3), 1, 1)
        await asyncio.sleep(0)
        await store.update(index, status="succeeded", remote_status="succeeded")

    await asyncio.wait_for(asyncio.gather(*(producer(i) for i in range(1, 41))), 5)
    await store.finish("succeeded")
    assert len(snapshots) == 121 and peak == 1 and writers == 0
    previous = None
    for snapshot in snapshots:
        if previous:
            for before, after in zip(previous["tasks"], snapshot["tasks"]):
                if before["remote_task_id"]:
                    assert after["remote_task_id"] == before["remote_task_id"]
                assert len(after["outputs"]) >= len(before["outputs"])
                assert [(o["result_index"], o["file"]) for o in after["outputs"][:len(before["outputs"])]] == [
                    (o["result_index"], o["file"]) for o in before["outputs"]
                ]
                if before["status"] == "succeeded":
                    assert after["status"] == "succeeded"
        previous = snapshot
    assert all(t["remote_task_id"] and t["outputs"] and t["status"] == "succeeded"
               for t in snapshots[-1]["tasks"])
    assert not list(store.path.glob(".image-api-*"))


def test_file_deleted_after_enumeration_fails_without_skipping(tmp_path, monkeypatch):
    for index in range(1, 4):
        (tmp_path / f"{index}.png").write_bytes(png())
    read = Path.read_bytes
    reads = []

    def delete_next(path):
        reads.append(path.name)
        result = read(path)
        if path.name == "1.png":
            path.with_name("2.png").unlink()
        return result

    monkeypatch.setattr(Path, "read_bytes", delete_next)
    with pytest.raises(ValueError, match="image 2; no images were skipped"):
        load_folder(str(tmp_path))
    assert reads == ["1.png", "2.png"]  # Never relabel the third file as item 2.


async def test_cancel_during_submission_cooldown_closes_workers_and_sessions(serve, tmp_path, monkeypatch):
    from grsai import batch_runner

    calls, sessions = [], []
    reached = asyncio.Event()
    real_session = batch_runner.aiohttp.ClientSession

    def session(*args, **kwargs):
        value = real_session(*args, **kwargs)
        sessions.append(value)
        return value

    async def generate(req):
        calls.append(1)
        return web.json_response({"error": "busy"}, status=429, headers={"Retry-After": "600"})

    cfg = await serve([("POST", "/v1/api/generate", generate)])
    batch = BatchRunner(cfg, KEY, make_plan(), "nano-banana-2", PARAMETERS, 4, "Clothes", tmp_path)
    monkeypatch.setattr(batch_runner.aiohttp, "ClientSession", session)
    original_wait = batch._wait_to_submit

    async def wait():
        if batch.cooldown_until > time.monotonic():
            reached.set()
        await original_wait()

    batch._wait_to_submit = wait
    before = asyncio.all_tasks()
    run = asyncio.create_task(batch.run())
    try:
        await asyncio.wait_for(reached.wait(), 3)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, 1)
    finally:
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)
    count = len(calls)
    await asyncio.sleep(0.02)
    assert len(calls) == count <= 4
    assert len(sessions) == 2 and all(s.closed for s in sessions)
    assert not [t for t in asyncio.all_tasks() - before
                if not t.done() and "BatchRunner" in t.get_coro().__qualname__]
    manifest = json.loads(batch.store.manifest.read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["summary"]["pending_tasks"] >= 36
