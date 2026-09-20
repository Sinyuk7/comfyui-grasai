"""Optional tests against actual ComfyUI source, not a replacement V3 implementation."""

import asyncio
import os
from types import SimpleNamespace

import pytest
import torch

pytestmark = pytest.mark.skipif(
    not os.environ.get("COMFYUI_PATH"), reason="Set COMFYUI_PATH to test the real host"
)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.syspath_prepend(os.environ["COMFYUI_PATH"])
    import comfy.cli_args

    comfy.cli_args.args.cpu = True
    import execution
    import nodes
    from comfy_api.latest import io
    from grsai.api_config import ImageAPIConfig
    from grsai.nodes import ImageGenerate

    class TestImage(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(node_id="ImageAPITestImage", inputs=[], outputs=[io.Image.Output()])

        @classmethod
        def execute(cls):
            return io.NodeOutput(torch.zeros(1, 2, 2, 3))

    monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, "ImageAPIConfig", ImageAPIConfig)
    monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, "ImageGenerate", ImageGenerate)
    monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, "ImageAPITestImage", TestImage)
    return execution, ImageGenerate


def inputs(linked=False):
    from grsai.api_settings import RuntimeAPIConfig
    from grsai.config import get_config

    return {
        "api_config": ["0", 0] if linked else RuntimeAPIConfig("key", get_config().base_url, "token"),
        "prompt": "text",
        "model": "nano-banana-2",
        "model.aspectRatio": "auto",
        "model.imageSize": "1K",
        "images": ["9", 0] if linked else torch.zeros(1, 2, 2, 3),
    }


def test_real_schema_and_list_wrapping(host):
    execution, node = host
    from comfy_api.latest import _io

    node.VALIDATE_CLASS()
    assert node.INPUT_IS_LIST and node.OUTPUT_IS_LIST == [True]
    assert node.NOT_IDEMPOTENT
    values, missing, v3_data = execution.get_input_data(inputs(), node, "17")
    assert not missing
    nested = _io.build_nested_inputs(values, v3_data)
    assert nested["model"] == {"model": ["nano-banana-2"], "aspectRatio": ["auto"], "imageSize": ["1K"]}


async def test_extension_registers_only_generic_node_ids(host):
    from grsai import comfy_entrypoint

    extension = await comfy_entrypoint()
    node_ids = [node.define_schema().node_id for node in await extension.get_node_list()]
    assert node_ids == [
        "ImageAPIConfig",
        "ImageGenerate",
        "ImageAPILoadImagesFromFolder",
        "BatchImageGenerate",
    ]


@pytest.mark.parametrize(
    "batches,count",
    [
        ([torch.zeros(1, 3, 4, 3)], 1),
        ([torch.zeros(2, 3, 4, 3)], 2),
        ([torch.zeros(2, 3, 4, 3), torch.zeros(1, 5, 2, 3)], 3),
    ],
)
async def test_real_host_invokes_once_and_outputs_all(host, monkeypatch, batches, count):
    execution, node = host
    from grsai import host as adapter
    from grsai.client import GrsaiClient

    calls, refreshes = [], []

    async def generate(self, request):
        self.submitted = True
        calls.append(request)
        return [torch.zeros(1, 3, 4, 3), torch.zeros(1, 5, 2, 3)]

    ui = SimpleNamespace(progress=None, refresh_balance=lambda: refreshes.append(1), stale=lambda: None)
    monkeypatch.setattr(adapter, "execution_ui", lambda *_: ui)
    monkeypatch.setattr(GrsaiClient, "generate", generate)
    values, _, v3_data = execution.get_input_data(inputs(), node, "17")
    values["images"] = batches
    output, _, _, pending = await execution.get_output_data("prompt", "17", node(), values, v3_data=v3_data)
    if pending:
        results = await asyncio.gather(*output)
        output = execution.merge_result_data([r.result for r in results], node)
    assert len(calls) == 1 and len(calls[0]["images"]) == count
    assert len(output) == 1 and [tuple(i.shape) for i in output[0]] == [(1, 3, 4, 3), (1, 5, 2, 3)]
    assert refreshes == [1]


async def test_reject_scalar_broadcast_before_network(host, monkeypatch):
    execution, node = host
    values, _, v3_data = execution.get_input_data(inputs(), node, "17")
    values["prompt"] = ["one", "two"]
    with pytest.raises(ValueError, match="exactly one"):
        output = await execution._async_map_node_over_list(
            "prompt", "17", node(), values, "execute", v3_data=v3_data
        )
        await asyncio.gather(*output)


async def test_fingerprints_and_distinct_node_cache_keys(host):
    _, node = host
    from comfy_execution.caching import CacheKeySetInputSignature
    from comfy_execution.graph import DynamicPrompt

    first = node.fingerprint_inputs()
    second = node.fingerprint_inputs()
    assert first != second
    prompt = DynamicPrompt(
        {
            "1": {"class_type": "ImageGenerate", "inputs": inputs()},
            "2": {"class_type": "ImageGenerate", "inputs": inputs()},
        }
    )

    class Unchanged:
        async def get(self, node_id):
            return "same"

    keys = CacheKeySetInputSignature(prompt, ["1", "2"], Unchanged())
    one = await keys.get_node_signature(prompt, "1")
    two = await keys.get_node_signature(prompt, "2")
    assert one != two


async def test_full_executor_cache_and_preview_save(host, monkeypatch, tmp_path):
    execution, node = host
    import copy
    import folder_paths
    from grsai import host as adapter
    from grsai.client import GrsaiClient

    monkeypatch.setattr(folder_paths, "output_directory", str(tmp_path / "output"))
    monkeypatch.setattr(folder_paths, "temp_directory", str(tmp_path / "temp"))
    (tmp_path / "output").mkdir()
    (tmp_path / "temp").mkdir()
    calls = []

    async def generate(self, request):
        self.submitted = True
        calls.append(request)
        await asyncio.sleep(0.001)
        return [torch.zeros(1, 3, 4, 3), torch.zeros(1, 5, 2, 3)]

    monkeypatch.setattr(GrsaiClient, "generate", generate)
    monkeypatch.setattr(
        adapter,
        "execution_ui",
        lambda *_: SimpleNamespace(progress=None, refresh_balance=lambda: None, stale=lambda: None),
    )
    server = SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *_: None)
    executor = execution.PromptExecutor(server, cache_args={"ram": 0, "ram_inactive": 0, "lru": 0})
    graph = {
        "0": {"class_type": "ImageAPIConfig", "inputs": {
            "api_key": "key", "base_url": "", "token": "token", "provider": "grsai"}},
        "9": {"class_type": "ImageAPITestImage", "inputs": {}},
        "1": {"class_type": "ImageGenerate", "inputs": inputs(linked=True)},
        "2": {"class_type": "ImageGenerate", "inputs": inputs(linked=True)},
        "3": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "grsai-test"}},
    }
    for attempt in range(2):
        await executor.execute_async(copy.deepcopy(graph), f"prompt-{attempt}", execute_outputs=["3", "4"])
        assert executor.success, executor.status_messages
        outputs = executor.history_result["outputs"]
        assert len(outputs["3"]["images"]) == 2
        assert len(outputs["4"]["images"]) == 2
    assert len(calls) == 4
    from PIL import Image

    assert sorted(Image.open(path).size for path in (tmp_path / "output").glob("*.png")) == [
        (2, 5),
        (2, 5),
        (4, 3),
        (4, 3),
    ]


@pytest.mark.parametrize("outcome", ["success", "failure", "invalid", "interrupted"])
async def test_real_node_balance_lifecycle(host, serve, monkeypatch, outcome):
    execution, node = host
    from aiohttp import web
    from comfy import model_management
    from grsai import host as adapter, nodes as node_module
    from grsai.balance import BalanceManager
    from grsai.errors import GrsaiError
    from server import PromptServer
    from test_config_images import png

    cfg = None
    posts, balances, events = [], [], []
    release = asyncio.Event()

    async def generate(req):
        posts.append(1)
        if outcome == "failure":
            return web.json_response(
                {"id": "failure", "status": "failed", "error": "original generation error"}
            )
        if outcome == "interrupted":
            model_management.interrupt_current_processing(True)
            return web.json_response({"id": "interrupted", "status": "running"})
        return web.json_response(
            {"id": "success", "status": "succeeded", "results": [{"url": cfg.base_url + "/image"}]}
        )

    async def image(req):
        return web.Response(body=png())

    async def balance(req):
        balances.append(1)
        await release.wait()
        return web.json_response({"code": 0, "data": {"credits": 0}})

    cfg = await serve(
        [
            ("POST", "/v1/api/generate", generate),
            ("GET", "/image", image),
            ("POST", "/client/openapi/getCredits", balance),
        ]
    )
    manager = BalanceManager(lambda payload, sid: events.append(payload))
    monkeypatch.setattr(adapter, "_manager", manager)
    monkeypatch.setattr(node_module, "get_config", lambda: cfg)
    server = SimpleNamespace(
        loop=asyncio.get_running_loop(), client_id="test-client", send_sync=lambda *args: None
    )
    monkeypatch.setattr(PromptServer, "instance", server, raising=False)
    flat = inputs()
    from grsai.api_settings import RuntimeAPIConfig

    flat["api_config"] = RuntimeAPIConfig("key", cfg.base_url, "token")
    if outcome == "invalid":
        flat["api_config"] = None
    values, _, v3_data = execution.get_input_data(
        flat,
        node,
        "17",
        extra_data={
            "extra_pnginfo": {"workflow": {"nodes": [{"id": 17, "properties": {"image_api_ui_token": "token"}}]}}
        },
    )

    async def run():
        tasks = await execution._async_map_node_over_list(
            "prompt", "17", node(), values, "execute", v3_data=v3_data
        )
        return await execution.resolve_map_node_over_list_results(tasks)

    try:
        if outcome == "failure":
            with pytest.raises(GrsaiError, match="original generation error"):
                await run()
        elif outcome == "invalid":
            with pytest.raises(ValueError):
                await run()
        elif outcome == "interrupted":
            with pytest.raises(model_management.InterruptProcessingException):
                await run()
        else:
            output = await asyncio.wait_for(run(), 2)
            assert output[0].result[0][0].shape == (1, 3, 4, 3)
        # Generation has returned while the independent balance endpoint is still blocked.
        release.set()
        await asyncio.sleep(0.05)
        expected = 0 if outcome in ("invalid", "interrupted") else 1
        assert len(balances) == expected
        assert len(posts) == (0 if outcome == "invalid" else 1)
        if expected:
            ready = next(e for e in events if e["state"] == "ready")
            assert ready["credits"] == 0 and ready["ui_token"] == "token"
    finally:
        model_management.interrupt_current_processing(False)
        release.set()
        await manager.close()
