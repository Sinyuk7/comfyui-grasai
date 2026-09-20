"""Real V3 schema, list wrapping, executor and downstream tests; network is local/mock only."""

import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

pytestmark = pytest.mark.skipif(not os.environ.get("COMFYUI_PATH"), reason="Requires actual ComfyUI")


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(os.environ["COMFYUI_PATH"])
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    import execution
    import folder_paths
    import nodes
    from comfy_api.latest import io
    from grsai.api_config import ImageAPIConfig
    from grsai.batch_nodes import BatchImageGenerate, ImageAPILoadImagesFromFolder
    from grsai import host as adapter

    class Prompts(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(node_id="TestBatchPrompts", inputs=[],
                             outputs=[io.String.Output(is_output_list=True)])

        @classmethod
        def execute(cls):
            return io.NodeOutput(["standing", "sitting"])

    for name, node in [("SinyukImageAPIBatchGenerate", BatchImageGenerate),
                       ("SinyukImageAPILoadFolder", ImageAPILoadImagesFromFolder),
                       ("SinyukImageAPIConfig", ImageAPIConfig),
                       ("TestBatchPrompts", Prompts)]:
        monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, name, node)
    (tmp_path / "output").mkdir()
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(folder_paths, "output_directory", str(tmp_path / "output"))
    monkeypatch.setattr(folder_paths, "temp_directory", str(tmp_path / "temp"))
    events, refreshes = [], []

    async def progress(payload):
        events.append(payload)

    monkeypatch.setattr(adapter, "execution_ui", lambda *_: SimpleNamespace(
        batch_progress=progress, refresh_balance=lambda: refreshes.append(1), stale=lambda: None))
    return execution, BatchImageGenerate, ImageAPILoadImagesFromFolder, events, refreshes


def inputs(linked=False):
    from grsai.api_settings import RuntimeAPIConfig
    from grsai.config import get_config

    api_config = ["0", 0] if linked else RuntimeAPIConfig("host-test-key", get_config().base_url, "token")
    return {"api_config": api_config, "model": "nano-banana-2", "model.aspectRatio": "auto",
            "model.imageSize": "1K", "prompt": "fallback", "max_concurrency": 4, "output_prefix": "Clothes"}


def config_node(api_key="host-test-key", token="token"):
    return {"class_type": "SinyukImageAPIConfig", "inputs": {
        "api_key": api_key, "base_url": "", "token": token, "provider": "grsai"
    }}


def test_schema(host):
    _, batch, folder, _, _ = host
    batch.VALIDATE_CLASS()
    folder.VALIDATE_CLASS()
    assert batch.INPUT_IS_LIST and batch.OUTPUT_NODE and batch.NOT_IDEMPOTENT
    assert batch.OUTPUT_IS_LIST == [True, False]
    assert folder.OUTPUT_IS_LIST == [False, True]
    schema = batch.define_schema()
    template = schema.inputs[0].template
    assert template.names[:2] == ["reference_1", "reference_2"]
    assert template.input.get_io_type() == "IMAGE_API_REFERENCES,IMAGE"


async def test_real_dynamic_wrapping_invokes_once(host, monkeypatch):
    execution, batch, _, events, refreshes = host
    from grsai.batch_runner import BatchRunner

    calls = []

    async def run(self):
        calls.append(self.plan)
        self.submitted = True
        return [torch.zeros(1, 3, 4, 3)] * self.plan.total, str(self.store.manifest)

    monkeypatch.setattr(BatchRunner, "run", run)
    flat = {**inputs(), "references.reference_1": ["upstream", 0],
            "references.reference_2": ["upstream", 1], "prompts": ["prompts", 0]}
    values, _, v3_data = execution.get_input_data(flat, batch, "17")
    values["references.reference_1"] = [torch.zeros(1, 2, 3, 3)]
    values["references.reference_2"] = [torch.zeros(2, 3, 4, 3)]
    values["prompts"] = ["pose-a", "pose-b"]
    output, _, _, pending = await execution.get_output_data("batch", "17", batch(), values, v3_data=v3_data)
    if pending:
        results = await asyncio.gather(*output)
        output = execution.merge_result_data([r.result for r in results], batch)
    assert len(calls) == 1 and calls[0].total == 4
    assert len(output[0]) == 4 and len(output[1]) == 1
    assert refreshes == [1]


async def test_full_executor_dual_outputs_prompt_list_and_repeat_queue(host, monkeypatch, tmp_path):
    execution, _, _, events, refreshes = host
    from grsai.client import GrsaiClient
    from test_config_images import png

    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "2.png").write_bytes(png((2, 5)))
    (folder / "1.png").write_bytes(png((4, 3)))
    calls = []

    async def generate(self, request):
        self.submitted = True
        calls.append(request)
        self.task_id = f"mock-{len(calls)}"
        self.remote_status = "succeeded"
        await self.accepted(self.task_id, self.remote_status)
        image = torch.zeros(1, 3, len(calls) + 1, 3)
        await self.result(image, 1, 1)
        return [image]

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    server = SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *_: None)
    executor = execution.PromptExecutor(server, cache_args={"ram": 0, "ram_inactive": 0, "lru": 0})
    graph = {
        "0": config_node(),
        "1": {"class_type": "SinyukImageAPILoadFolder", "inputs": {"folder": str(folder)}},
        "2": {"class_type": "TestBatchPrompts", "inputs": {}},
        "3": {"class_type": "SinyukImageAPIBatchGenerate", "inputs": {
            **inputs(linked=True), "references.reference_1": ["1", 0], "references.reference_2": ["1", 1],
            "prompts": ["2", 0]}},
        "4": {"class_type": "PreviewImage", "inputs": {"images": ["3", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "downstream"}},
    }
    validation = await execution.validate_prompt("validate-batch", copy.deepcopy(graph), None)
    assert validation[0], validation
    for attempt in range(2):
        await executor.execute_async(copy.deepcopy(graph), f"batch-{attempt}", execute_outputs=["3", "4", "5"])
        assert executor.success, executor.status_messages
        assert len(executor.history_result["outputs"]["4"]["images"]) == 4
        assert len(executor.history_result["outputs"]["5"]["images"]) == 4
    assert len(calls) == 8 and len(refreshes) == 2
    manifests = list((tmp_path / "output" / "image_api").glob("*/manifest.json"))
    assert len(manifests) == 2
    for path in manifests:
        data = json.loads(path.read_text())
        assert data["total_tasks"] == 4
        assert data["bases"][0]["references"][0]["source"]["filename"] == "1.png"
        assert data["bases"][0]["references"][1]["source"]["filename"] == "unknown"
        assert "host-test-key" not in path.read_text()
    assert [p["prompt"] for p in calls] == ["standing", "sitting"] * 4


async def test_batch_is_terminal_and_all_failed_still_returns_report(host, monkeypatch, tmp_path):
    execution, _, _, _, _ = host
    from grsai.client import GrsaiClient
    from grsai.errors import GrsaiError
    from test_config_images import png

    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "1.png").write_bytes(png())

    async def generate(self, request):
        self.submitted = True
        raise GrsaiError("failed")

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    graph = {"0": config_node(),
             "1": {"class_type": "SinyukImageAPILoadFolder", "inputs": {"folder": str(folder)}},
             "2": {"class_type": "SinyukImageAPIBatchGenerate", "inputs": {
                 **inputs(linked=True), "references.reference_1": ["1", 0]}},
             "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}}}
    executor = execution.PromptExecutor(SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *_: None),
                                        cache_args={"ram": 0, "ram_inactive": 0, "lru": 0})
    await executor.execute_async(graph, "failed-batch", execute_outputs=["2", "3"])
    assert executor.success, executor.status_messages
    path = executor.history_result["outputs"]["2"]["image_api_manifest"][0]
    assert json.loads(Path(path).read_text())["status"] == "failed"


async def test_distinct_batch_cache_keys(host):
    _, batch, _, _, _ = host
    from comfy_execution.caching import CacheKeySetInputSignature
    from comfy_execution.graph import DynamicPrompt

    assert batch.fingerprint_inputs() != batch.fingerprint_inputs()
    graph = DynamicPrompt({str(i): {"class_type": "SinyukImageAPIBatchGenerate", "inputs": inputs()} for i in (1, 2)})

    class Unchanged:
        async def get(self, node_id):
            return "same"

    keys = CacheKeySetInputSignature(graph, ["1", "2"], Unchanged())
    assert await keys.get_node_signature(graph, "1") != await keys.get_node_signature(graph, "2")


@pytest.mark.parametrize("case", ["mismatch", "hole", "key_list", "prompt_type", "concurrency"])
async def test_host_preflight_never_submits(host, monkeypatch, case):
    execution, batch, _, _, refreshes = host
    from grsai.client import GrsaiClient

    async def forbidden(*_):
        pytest.fail("Preflight must not invoke generation")

    monkeypatch.setattr(GrsaiClient, "generate", forbidden)
    name = "reference_3" if case == "hole" else "reference_2"
    flat = {**inputs(), "references.reference_1": ["x", 0], f"references.{name}": ["x", 1]}
    values, _, v3_data = execution.get_input_data(flat, batch, "17")
    values["references.reference_1"] = [torch.zeros(2, 2, 3, 3)]
    values[f"references.{name}"] = [torch.zeros(3 if case == "mismatch" else 2, 2, 3, 3)]
    if case == "key_list":
        from grsai.api_settings import RuntimeAPIConfig
        from grsai.config import get_config

        values["api_config"] = [RuntimeAPIConfig("key-1", get_config().base_url),
                                RuntimeAPIConfig("key-2", get_config().base_url)]
    if case == "prompt_type":
        values["prompt"] = [123]
    if case == "concurrency":
        values["max_concurrency"] = [11]
    with pytest.raises(ValueError):
        output = await execution._async_map_node_over_list("preflight", "17", batch(), values,
                                                          "execute", v3_data=v3_data)
        await execution.resolve_map_node_over_list_results(output)
    assert not refreshes


@pytest.mark.parametrize("prompts", [[], [[]]])
async def test_host_empty_prompt_list_falls_back(host, monkeypatch, prompts):
    execution, batch, _, _, _ = host
    from grsai.batch_runner import BatchRunner

    async def run(self):
        assert self.plan.prompts == ("fallback",) and self.plan.total == 2
        return [], str(self.store.manifest)

    monkeypatch.setattr(BatchRunner, "run", run)
    flat = {**inputs(), "references.reference_1": ["x", 0], "prompts": ["y", 0]}
    values, _, v3_data = execution.get_input_data(flat, batch, "17")
    values["references.reference_1"] = [torch.zeros(2, 2, 3, 3)]
    values["prompts"] = prompts
    output = await execution._async_map_node_over_list("empty", "17", batch(), values, "execute", v3_data=v3_data)
    resolved = await execution.resolve_map_node_over_list_results(output)
    assert len(resolved) == 1


async def test_real_batch_adapter_and_local_http(host, serve, monkeypatch, tmp_path):
    from aiohttp import web
    from grsai import batch_nodes
    from test_config_images import png

    execution, batch, _, events, refreshes = host
    cfg = None
    calls, auth = [], []

    async def generate(req):
        calls.append(await req.json())
        return web.json_response({"id": f"http-{len(calls)}", "status": "running"})

    async def result(req):
        paths = list((tmp_path / "output" / "image_api").glob("*/manifest.json"))
        data = json.loads(paths[0].read_text())
        assert any(t["remote_task_id"] == req.query["id"] for t in data["tasks"])
        return web.json_response({"id": req.query["id"], "status": "succeeded",
                                  "results": [{"url": cfg.base_url + "/image"}]})

    async def image(req):
        auth.append(req.headers.get("Authorization"))
        return web.Response(body=png())

    cfg = await serve([("POST", "/v1/api/generate", generate), ("GET", "/v1/api/result", result),
                       ("GET", "/image", image)])
    monkeypatch.setattr(batch_nodes, "get_config", lambda: cfg)
    flat = {**inputs(), "references.reference_1": ["x", 0], "prompts": ["y", 0]}
    values, _, v3_data = execution.get_input_data(flat, batch, "17")
    from grsai.api_settings import RuntimeAPIConfig

    values["api_config"] = [RuntimeAPIConfig("host-test-key", cfg.base_url, "token")]
    values["references.reference_1"] = [torch.zeros(2, 2, 3, 3)]
    values["prompts"] = [["first", "second"]]
    output = await execution._async_map_node_over_list("http", "17", batch(), values, "execute", v3_data=v3_data)
    resolved = await execution.resolve_map_node_over_list_results(output)
    assert len(resolved) == 1 and len(resolved[0].result[0]) == 4
    assert len(calls) == 4 and auth == [None] * 4 and refreshes == [1]
    assert events[0]["total"] == 4 and events[-1]["completed"] == 4


async def test_folder_content_changes_invalidate_real_executor_cache(host, monkeypatch, tmp_path):
    execution, _, _, _, _ = host
    from grsai.batch_runner import BatchRunner
    from test_config_images import png

    folder = tmp_path / "changing"
    folder.mkdir()
    (folder / "1.png").write_bytes(png((4, 3)))
    plans = []

    async def run(self):
        plans.append(self.plan)
        return [], str(self.store.manifest)

    monkeypatch.setattr(BatchRunner, "run", run)
    graph = {"0": config_node(),
             "1": {"class_type": "SinyukImageAPILoadFolder", "inputs": {"folder": str(folder)}},
             "2": {"class_type": "SinyukImageAPIBatchGenerate", "inputs": {
                 **inputs(linked=True), "references.reference_1": ["1", 0]}}}
    executor = execution.PromptExecutor(SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *_: None),
                                       cache_args={"ram": 0, "ram_inactive": 0, "lru": 100})
    for attempt in range(4):
        if attempt == 1:
            (folder / "1.png").write_bytes(png((7, 2)))
        elif attempt == 2:
            (folder / "2.png").write_bytes(png((2, 5)))
        elif attempt == 3:
            (folder / "1.png").unlink()
        await executor.execute_async(copy.deepcopy(graph), f"changed-{attempt}", execute_outputs=["2"])
        assert executor.success, executor.status_messages
    assert [p.base_count for p in plans] == [1, 1, 2, 1]
    assert plans[0].columns[0].sources[0].sha256 != plans[1].columns[0].sources[0].sha256
    assert [p.columns[0].images[0].shape for p in plans] == [
        (1, 3, 4, 3), (1, 2, 7, 3), (1, 2, 7, 3), (1, 5, 2, 3),
    ]
    assert [s.filename for s in plans[-1].columns[0].sources] == ["2.png"]


async def test_test_key_persistence_boundary_in_actual_saved_pngs(host, monkeypatch, tmp_path, caplog):
    execution, _, _, events, _ = host
    from comfy.cli_args import args
    from PIL import Image
    from grsai.client import GrsaiClient
    from test_config_images import png

    key = "fake-key-for-metadata-audit-only"
    monkeypatch.setattr(args, "disable_metadata", False)
    folder = tmp_path / "metadata-input"
    folder.mkdir()
    (folder / "1.png").write_bytes(png())

    async def generate(self, request):
        assert self.api_key == key
        self.submitted = True
        self.task_id, self.remote_status = "metadata-task", "succeeded"
        await self.accepted(self.task_id, self.remote_status)
        image = torch.zeros(1, 2, 3, 3)
        await self.result(image, 1, 1)
        return [image]

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    graph = {"0": config_node(key),
             "1": {"class_type": "SinyukImageAPILoadFolder", "inputs": {"folder": str(folder)}},
             "2": {"class_type": "SinyukImageAPIBatchGenerate", "inputs": {
                 **inputs(linked=True), "references.reference_1": ["1", 0]}},
             "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "downstream"}}}
    workflow = {"nodes": [{"id": 0, "type": "SinyukImageAPIConfig",
                            "widgets_values": [key, "", "token", "grsai"]},
                          {"id": 2, "type": "SinyukImageAPIBatchGenerate",
                           "widgets_values": ["nano-banana-2", "auto", "1K", "fallback", 4, "Clothes"],
                           "properties": {"image_api_ui_token": "test-token"}}]}
    executor = execution.PromptExecutor(SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *_: None),
                                       cache_args={"ram": 0, "ram_inactive": 0, "lru": 0})
    await executor.execute_async(copy.deepcopy(graph), "metadata-audit",
                                 extra_data={"extra_pnginfo": {"workflow": workflow}}, execute_outputs=["2", "3"])
    assert executor.success, executor.status_messages
    internal = list((tmp_path / "output" / "image_api").glob("*/*"))
    assert len(internal) == 2
    for path in internal:
        assert key not in path.name and key.encode() not in path.read_bytes()
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert not image.info
    downstream = list((tmp_path / "output").glob("downstream*.png"))
    assert len(downstream) == 1
    with Image.open(downstream[0]) as image:
        # Deliberately prove the documented host risk, not a claim of secret storage.
        embedded_api = json.loads(image.info["prompt"])
        embedded_workflow = json.loads(image.info["workflow"])
    # The host annotates the prompt with cache fingerprints (including NaN).
    assert {node_id: {k: v for k, v in node.items() if k != "is_changed"}
            for node_id, node in embedded_api.items()} == graph
    assert embedded_workflow == workflow
    assert embedded_api["0"]["inputs"]["api_key"] == key
    assert json.dumps(embedded_api).count(key) == 1
    assert json.dumps(embedded_workflow).count(key) == 1
    assert key not in json.dumps(events) and key not in caplog.text
    assert key not in json.dumps(executor.history_result["outputs"])
