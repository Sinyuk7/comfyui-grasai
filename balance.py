"""Independent short-lived account balance query and lifecycle-owned refreshes."""

import asyncio
import math
import time
from datetime import datetime, timezone

import aiohttp

from .client import cancellable
from .errors import GrsaiError, clean_message


def parse_credits(data, token):
    if not isinstance(data, dict):
        raise GrsaiError("Balance response must be a JSON object.")
    if type(data.get("code")) not in (int, float) or data["code"] != 0:
        raise GrsaiError(clean_message(data.get("msg", "Balance business error."), (token,)))
    credits = data.get("data", {}).get("credits") if isinstance(data.get("data"), dict) else None
    if type(credits) not in (int, float):
        raise GrsaiError("Balance credits must be a finite number.")
    try:
        finite = math.isfinite(credits)
    except OverflowError:
        finite = False
    if not finite:
        raise GrsaiError("Balance credits must be a finite number.")
    return credits


async def query_balance(base_url, token, check_cancel=lambda: None):
    async def query():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=15)) as session:
            async with session.post(
                base_url + "/client/openapi/getCredits",
                json={"token": token},
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise GrsaiError(f"Balance HTTP {response.status}.")
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    raise GrsaiError("Balance response is not JSON.") from None
                return parse_credits(data, token)

    try:
        return await cancellable(query(), check_cancel)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise GrsaiError("Balance query failed or timed out.") from None


class BalanceManager:
    """Lives exclusively on the server loop, not the per-prompt execution loop."""

    def __init__(self, emit, interrupted=lambda: False):
        self.emit = emit
        self.interrupted = interrupted
        self.tasks = {}
        self.pending = set()
        self.closed = False

    def start(self, node_id, client_id, base_url, token, sequence, ui_token=None):
        if self.closed:
            return
        scope = (client_id, node_id)
        previous = self.tasks.get(scope)
        if previous:
            previous.cancel()
        payload = {"node_id": node_id, "sequence": sequence, "ui_token": ui_token}
        if self.interrupted():
            self.emit({**payload, "state": "stale"}, client_id)
            return
        task = asyncio.create_task(self._refresh(payload, client_id, base_url, token))
        self.tasks[scope] = task
        self.pending.add(task)

        def done(completed):
            self.pending.discard(completed)
            if self.tasks.get(scope) is completed:
                self.tasks.pop(scope, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)

    async def _refresh(self, payload, client_id, base_url, token):
        def check():
            if self.interrupted():
                raise asyncio.CancelledError()

        self.emit({**payload, "state": "loading"}, client_id)
        try:
            credits = await query_balance(base_url, token, check)
        except asyncio.CancelledError:
            self.emit({**payload, "state": "stale"}, client_id)
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, GrsaiError) else "Balance query failed."
            self.emit({**payload, "state": "error", "message": clean_message(message, (token,))}, client_id)
        else:
            self.emit(
                {
                    **payload,
                    "state": "ready",
                    "credits": credits,
                    "queried_at": datetime.now(timezone.utc).isoformat(),
                },
                client_id,
            )

    async def close(self, _app=None):
        self.closed = True
        tasks = list(self.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.pending.clear()


def query_sequence():
    # Milliseconds fit exactly in JavaScript numbers; fractional milliseconds order runs.
    return time.time_ns() / 1_000_000
