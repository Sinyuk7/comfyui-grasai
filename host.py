"""Server-loop ownership for optional balance and progress updates."""

import logging

from aiohttp import web

from .balance import BalanceManager, query_sequence
from .config import get_config, normalize_base_url
from .model_status import query_model_status

logger = logging.getLogger(__name__)
_manager = None
_OFFICIAL_BASE_URLS = {"https://grsaiapi.com", "https://grsai.dakka.com.cn"}


def model_status_base_url(value, config):
    base_url = normalize_base_url(value, "Base URL")
    if base_url not in {*_OFFICIAL_BASE_URLS, config.base_url}:
        raise ValueError("Model status is disabled for this Base URL.")
    return base_url


def install_host():
    global _manager
    if _manager is not None:
        return
    from comfy import model_management
    from server import PromptServer

    server = PromptServer.instance

    def emit(payload, client_id):
        try:
            if client_id is not None:
                server.send_sync("grsai.balance", payload, client_id)
        except Exception:
            logger.warning("GRSAI balance display unavailable")

    _manager = BalanceManager(emit, model_management.processing_interrupted)
    server.app.on_cleanup.append(_manager.close)

    @server.routes.get("/grsai/config")
    async def catalog(_request):
        return web.json_response(get_config().public_catalog(), headers={"Cache-Control": "no-store"})

    @server.routes.post("/grsai/model-status")
    async def model_status(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"ok": False}, status=400)
        model = body.get("model")
        if not isinstance(model, str) or model not in get_config().models:
            return web.json_response({"ok": False}, status=400)
        try:
            base_url = model_status_base_url(body.get("base_url", ""), get_config())
            available, error = await query_model_status(base_url, model)
        except Exception:
            # Availability is advisory. Do not expose upstream diagnostics for failed checks.
            return web.json_response({"ok": False})
        return web.json_response({"ok": True, "available": available, "error": error})


def _ui_token(extra_pnginfo, node_id):
    if not isinstance(extra_pnginfo, dict):
        return None
    workflow = extra_pnginfo.get("workflow", {})
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes", []), list):
        return None
    for node in workflow.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("properties", {}), dict):
            continue
        if str(node.get("id")) == str(node_id):
            token = node.get("properties", {}).get("grsai_ui_token")
            return token if isinstance(token, str) and len(token) <= 128 else None
    return None


class ExecutionUI:
    def __init__(self, hidden, config, balance_token):
        from server import PromptServer

        self.server = PromptServer.instance
        self.node_id = str(hidden.unique_id)
        # Capture the client now; server.client_id changes with subsequent queued prompts.
        self.client_id = self.server.client_id
        self.config = config
        self.balance_token = balance_token
        self.sequence = query_sequence()
        self.token = _ui_token(hidden.extra_pnginfo, self.node_id)

    def send(self, event, payload):
        try:
            if self.client_id is not None:
                self.server.send_sync(
                    event,
                    {"node_id": self.node_id, "sequence": self.sequence, "ui_token": self.token, **payload},
                    self.client_id,
                )
        except Exception:
            logger.warning("GRSAI UI update unavailable")

    async def progress(self, stage, value, task_id, details=None):
        payload = {"stage": stage, "progress": value, "task_id": task_id, **(details or {})}
        self.send("grsai.progress", payload)
        # Download is a separate phase in the node UI. Do not reset the native
        # generation bar to zero when local result transfer starts.
        if value is not None and stage in {"running", "succeeded"}:
            try:
                from comfy_api.latest import ComfyAPI

                await ComfyAPI().execution.set_progress(value, 100, node_id=self.node_id)
            except Exception:
                logger.debug("GRSAI progress display unavailable")

    async def batch_progress(self, payload):
        self.send("grsai.batch", payload)
        try:
            from comfy_api.latest import ComfyAPI

            await ComfyAPI().execution.set_progress(payload["completed"], payload["total"], node_id=self.node_id)
        except Exception:
            logger.debug("GRSAI batch progress display unavailable")

    def stale(self):
        self.send("grsai.balance", {"state": "stale"})

    def refresh_balance(self):
        try:
            if not self.balance_token:
                self.send("grsai.balance", {"state": "unavailable"})
            elif _manager is not None:
                self.server.loop.call_soon_threadsafe(
                    _manager.start,
                    self.node_id,
                    self.client_id,
                    self.config.base_url,
                    self.balance_token,
                    self.sequence,
                    self.token,
                )
        except Exception:
            self.send("grsai.balance", {"state": "error", "message": "Balance refresh unavailable."})


def execution_ui(hidden, config, balance_token):
    return ExecutionUI(hidden, config, balance_token)
