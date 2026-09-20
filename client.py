"""Submit-once asynchronous generation, interruptible polling and downloads."""

import asyncio
import json
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import aiohttp

from .config import Config
from .diagnostics import log_event, new_run_id
from .errors import GrsaiError, clean_message
from .images import decode_image

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}


async def cancellable(awaitable, check_cancel=lambda: None):
    """Check the host signal while I/O is pending, then await cancellation cleanup."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            check_cancel()
            done, _ = await asyncio.wait({task}, timeout=0.2)
            if done:
                check_cancel()
                return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class GrsaiClient:
    def __init__(self, config: Config, api_key, check_cancel=lambda: None, progress=None,
                 *, accepted=None, result=None, pressure=None, sessions=None, log_context=None):
        self.config = config
        self.api_key = api_key
        self.check_cancel = check_cancel
        self.progress = progress
        self.task_id = None
        self.submitted = False
        self.submission_unknown = False
        self.accepted = accepted
        self.result = result
        self.pressure = pressure
        self.sessions = sessions
        self.http_status = None
        self.remote_status = None
        self.generation_progress = None
        self._secrets = (api_key,)
        self.log_context = dict(log_context or {"run_id": new_run_id()})

    def _error(self, message, index=None):
        return GrsaiError(clean_message(message, self._secrets), self.task_id, index)

    async def _notify(self, stage, progress=None, details=None):
        if self.progress:
            await self.progress(stage, progress, self.task_id, details or {})

    async def _wait(self, seconds):
        await asyncio.sleep(seconds)

    def _timeout(self, total):
        return aiohttp.ClientTimeout(total=total, connect=self.config.transport.connect_timeout_seconds)

    async def _json(self, session, method, path, timeout, **kwargs):
        async with session.request(
            method,
            self.config.base_url + path,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self._timeout(timeout),
            allow_redirects=False,
            **kwargs,
        ) as response:
            raw = await response.read()
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                data = None
            return response.status, data, retry_after(response.headers.get("Retry-After"))

    def _remember_id(self, data):
        if not isinstance(data, dict) or "id" not in data:
            return
        task_id = data["id"]
        if (
            not isinstance(task_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", task_id)
            or self.api_key in task_id
        ):
            raise self._error("Invalid task ID in upstream response.")
        if self.task_id and self.task_id != task_id:
            raise self._error("Upstream returned a different task ID.")
        if not self.task_id:
            self.task_id = task_id
            logger.info("GRSAI task accepted task_id=%s", task_id)
            log_event("task.accepted", **self.log_context, task_id=task_id)

    def _validate(self, status, data):
        self._remember_id(data)
        if not 200 <= status < 300:
            detail = (
                data.get("error", "Request rejected.") if isinstance(data, dict) else "Non-JSON response."
            )
            raise self._error(f"HTTP {status}: {clean_message(detail, self._secrets)}")
        if not isinstance(data, dict):
            raise self._error("Expected a JSON object from generation service.")
        state = data.get("status")
        if state in ("failed", "violation"):
            raise self._error(
                f"{state}: {clean_message(data.get('error', 'No reason provided.'), self._secrets)}"
            )
        if state not in ("running", "succeeded"):
            raise self._error("Unknown or missing generation status.")
        if state == "running" and not self.task_id:
            raise self._error("Running response has no task ID; do not automatically resubmit.")
        return state

    async def _receive(self, status, data, delay):
        self.http_status = status
        previous = self.task_id
        self._remember_id(data)
        state = data.get("status") if isinstance(data, dict) else None
        if isinstance(state, str) and state.lower() in {
            "create", "queued", "running", "succeeded", "success", "failed", "cancel", "violation"
        }:
            self.remote_status = state
        # This is an awaited business boundary, not a best-effort UI notification.
        if self.task_id and previous is None and self.accepted:
            await self.accepted(self.task_id, self.remote_status)
        if (status == 429 or 500 <= status <= 599) and delay is not None and self.pressure:
            await self.pressure(delay)

    @asynccontextmanager
    async def _sessions(self):
        if self.sessions is not None:
            yield self.sessions
        else:
            async with aiohttp.ClientSession() as api, aiohttp.ClientSession() as cdn:
                yield api, cdn

    async def generate(self, request):
        self._secrets = (self.api_key, request.get("prompt", ""), *request.get("images", []))
        try:
            operation = self._generate(request)
            limit = self.config.transport.task_timeout_seconds
            if limit is not None:
                operation = asyncio.wait_for(operation, timeout=limit)
            return await cancellable(operation, self.check_cancel)
        except asyncio.TimeoutError:
            raise self._error(
                "Local total task deadline reached; remote task may still be running."
            ) from None
        except BaseException:
            if self.submitted:
                logger.info(
                    "GRSAI local execution ended task_id=%s; remote state is not changed",
                    self.task_id or "unknown",
                )
            raise

    async def _generate(self, request):
        t = self.config.transport
        # No default authentication on either session: credentials are API-request-only.
        async with self._sessions() as (api, cdn):
            await self._notify("submitting")
            self.check_cancel()
            self.submitted = True
            try:
                status, data, delay = await self._json(
                    api, "POST", "/v1/api/generate", t.submit_timeout_seconds, json=request
                )
            except (aiohttp.ClientError, asyncio.TimeoutError):
                self.submission_unknown = True
                raise self._error(
                    "Submission response lost; a remote task may already exist. POST was not retried."
                ) from None
            await self._receive(status, data, delay)
            try:
                state = self._validate(status, data)
            except GrsaiError as exc:
                if not self.task_id:
                    raise self._error(
                        f"{exc} Remote task creation is uncertain; POST was not retried."
                    ) from None
                raise
            backoff = min(t.poll_interval_seconds, t.retry_backoff_max_seconds)
            while state == "running":
                progress = data.get("progress")
                if (
                    type(progress) not in (int, float)
                    or not math.isfinite(progress)
                    or not 0 <= progress <= 100
                ):
                    progress = self.generation_progress
                else:
                    progress = max(progress, self.generation_progress or 0)
                    self.generation_progress = progress
                await self._notify("running", progress)
                await self._wait(t.poll_interval_seconds)
                retry_count = 0
                while True:
                    try:
                        status, next_data, delay = await self._json(
                            api,
                            "GET",
                            "/v1/api/result",
                            t.poll_request_timeout_seconds,
                            params={"id": self.task_id},
                        )
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        status, next_data, delay = 503, None, None
                    await self._receive(status, next_data, delay)
                    if status in RETRYABLE_HTTP_STATUSES:
                        # Explicit terminal task states take precedence over transient HTTP status.
                        if isinstance(next_data, dict) and next_data.get("status") in ("failed", "violation"):
                            self._validate(status, next_data)
                        retry_count += 1
                        if retry_count > t.poll_retry_limit:
                            raise self._error("Task status query failed after bounded retries.")
                        if retry_count == 1:
                            log_event(
                                "poll.reconnecting",
                                level=logging.WARNING,
                                **self.log_context,
                                task_id=self.task_id,
                                http_status=status,
                            )
                        await self._notify("reconnecting")
                        await self._wait(max(backoff, delay or 0))
                        backoff = min(backoff * 2, t.retry_backoff_max_seconds)
                        continue
                    if retry_count:
                        log_event(
                            "poll.recovered",
                            **self.log_context,
                            task_id=self.task_id,
                            attempts=retry_count,
                        )
                    data = next_data
                    state = self._validate(status, data)
                    backoff = min(t.poll_interval_seconds, t.retry_backoff_max_seconds)
                    break
            results = data.get("results")
            if not isinstance(results, list) or not results:
                raise self._error("Succeeded response contains no images.")
            urls = []
            for index, result in enumerate(results, 1):
                url = result.get("url") if isinstance(result, dict) else None
                if not isinstance(url, str):
                    raise self._error("Missing result URL.", index)
                try:
                    parsed = urlsplit(url)
                    valid = (
                        parsed.scheme in ("http", "https")
                        and parsed.hostname
                        and not parsed.username
                        and not parsed.password
                    )
                except ValueError:
                    valid = False
                if not valid or self.api_key in url:
                    raise self._error("Invalid result URL.", index)
                urls.append(url)
            images = []
            await self._notify("downloading", 0, {"completed": 0, "total": len(urls)})
            for index, url in enumerate(urls, 1):
                image = await self._download(cdn, url, index)
                if self.result:
                    await self.result(image, index, len(urls))
                images.append(image)
                await self._notify(
                    "downloading",
                    index * 100 / len(urls),
                    {"completed": index, "total": len(urls)},
                )
            await self._notify("succeeded", 100)
            return images

    async def _download(self, session, url, index):
        t = self.config.transport
        for attempt in range(t.download_retry_limit + 1):
            delay = None
            try:
                async with session.get(url, timeout=self._timeout(t.download_timeout_seconds)) as response:
                    delay = retry_after(response.headers.get("Retry-After"))
                    if not 200 <= response.status < 300:
                        if response.status not in RETRYABLE_HTTP_STATUSES:
                            raise self._error(f"Image download HTTP {response.status}.", index)
                        if attempt == t.download_retry_limit:
                            raise self._error(
                                "Image download failed after bounded retries; generation was not repeated.", index
                            )
                    else:
                        data = await response.read()
                        # Decode on the execution thread; do not leave detached CPU workers on cancel.
                        image = decode_image(data)
                        self.check_cancel()
                        return image
            except ValueError as exc:
                raise self._error(str(exc), index) from None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == t.download_retry_limit:
                    raise self._error(
                        "Image download failed after bounded retries; generation was not repeated.", index
                    ) from None
            await self._wait(max(min(2**attempt, t.retry_backoff_max_seconds), delay or 0))
