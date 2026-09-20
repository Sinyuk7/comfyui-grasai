import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "grsai", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
package = importlib.util.module_from_spec(spec)
sys.modules["grsai"] = package
spec.loader.exec_module(package)

if os.environ.get("COMFYUI_PATH"):
    sys.path.insert(0, os.environ["COMFYUI_PATH"])


@pytest.fixture
def raw_config():
    return json.loads((ROOT / "grsai_config.example.json").read_text())


@pytest.fixture
def config(raw_config):
    from grsai.config import parse_config

    return parse_config(raw_config)


@pytest_asyncio.fixture
async def serve(config):
    runners = []

    async def start(routes):
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        runner = web.AppRunner(app, shutdown_timeout=0.1)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        transport = replace(config.transport, poll_interval_seconds=0.005, retry_backoff_max_seconds=0.01)
        return replace(config, base_url=f"http://127.0.0.1:{port}", transport=transport)

    yield start
    for runner in runners:
        await runner.cleanup()
