"""RunningHub upload, submit-once generation, polling, and result download."""

import asyncio
import logging
import re
from urllib.parse import urlsplit

import aiohttp

from .client import GrsaiClient, cancellable
from .diagnostics import log_event
from .errors import GrsaiError, clean_message

logger = logging.getLogger(__name__)


class RunningHubClient(GrsaiClient):
    def __init__(self, *args, endpoint, **kwargs):
        super().__init__(*args, **kwargs)
        self.endpoint = endpoint

    def _remember_id(self, data):
        if not isinstance(data, dict) or "taskId" not in data:
            return
        task_id = data["taskId"]
        if (
            not isinstance(task_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", task_id)
            or self.api_key in task_id
        ):
            raise self._error("Invalid task ID in RunningHub response.")
        if self.task_id and self.task_id != task_id:
            raise self._error("RunningHub returned a different task ID.")
        if not self.task_id:
            self.task_id = task_id
            logger.info("RunningHub task accepted task_id=%s", task_id)
            log_event("task.accepted", **self.log_context, provider="runninghub", task_id=task_id)

    def _validate(self, status, data):
        self._remember_id(data)
        if not 200 <= status < 300:
            detail = data.get("errorMessage") if isinstance(data, dict) else "Non-JSON response."
            raise self._error(f"HTTP {status}: {clean_message(detail or 'Request rejected.', self._secrets)}")
        if not isinstance(data, dict):
            raise self._error("Expected a JSON object from RunningHub.")
        error_code = data.get("errorCode")
        error_message = data.get("errorMessage")
        state = data.get("status")
        if not isinstance(state, str):
            raise self._error("Unknown or missing RunningHub task status.")
        state = state.upper()
        if state in {"FAILED", "CANCEL"} or error_code or error_message:
            detail = error_message or data.get("failedReason") or "No reason provided."
            raise self._error(f"{state}: {clean_message(detail, self._secrets)}")
        if state not in {"CREATE", "QUEUED", "RUNNING", "SUCCESS"}:
            raise self._error("Unknown or missing RunningHub task status.")
        if state != "SUCCESS" and not self.task_id:
            raise self._error("RunningHub response has no task ID; do not automatically resubmit.")
        return state

    async def upload_image(self, session, content, index=1):
        t = self.config.transport
        for attempt in range(t.download_retry_limit + 1):
            form = aiohttp.FormData()
            form.add_field("file", content, filename=f"reference-{index:02d}.png", content_type="image/png")
            delay = None
            try:
                status, data, delay = await self._json(
                    session,
                    "POST",
                    "/openapi/v2/media/upload/binary",
                    t.submit_timeout_seconds,
                    data=form,
                )
                self.http_status = status
                url = (data.get("data") or {}).get("download_url") if isinstance(data, dict) else None
                if 200 <= status < 300 and isinstance(data, dict) and data.get("code") == 0 and isinstance(url, str) and url:
                    return url
                if status != 429 and status < 500:
                    message = data.get("message") if isinstance(data, dict) else "Invalid upload response."
                    raise self._error(f"Reference image upload failed: {clean_message(message, self._secrets)}", index)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == t.download_retry_limit:
                    raise self._error("Reference image upload failed after bounded retries.", index) from None
            if attempt == t.download_retry_limit:
                raise self._error("Reference image upload failed after bounded retries.", index)
            await self._wait(max(min(2**attempt, t.retry_backoff_max_seconds), delay or 0))

    async def _upload_images(self, contents):
        async with aiohttp.ClientSession() as session:
            return [await self.upload_image(session, content, index) for index, content in enumerate(contents, 1)]

    async def upload_images(self, contents):
        return await cancellable(self._upload_images(contents), self.check_cancel)

    async def _generate(self, request):
        t = self.config.transport
        async with self._sessions() as (api, cdn):
            await self._notify("submitting")
            self.check_cancel()
            self.submitted = True
            try:
                status, data, delay = await self._json(
                    api, "POST", self.endpoint, t.submit_timeout_seconds, json=request
                )
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise self._error(
                    "Submission response lost; a remote task may already exist. POST was not retried."
                ) from None
            await self._receive(status, data, delay)
            try:
                state = self._validate(status, data)
            except GrsaiError as exc:
                if not self.task_id:
                    raise self._error(f"{exc} Remote task creation is uncertain; POST was not retried.") from None
                raise
            backoff = min(t.poll_interval_seconds, t.retry_backoff_max_seconds)
            while state != "SUCCESS":
                await self._notify("running", None)
                await self._wait(t.poll_interval_seconds)
                retry_count = 0
                while True:
                    try:
                        status, next_data, delay = await self._json(
                            api,
                            "POST",
                            "/openapi/v2/query",
                            t.poll_request_timeout_seconds,
                            json={"taskId": self.task_id},
                        )
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        status, next_data, delay = 503, None, None
                    await self._receive(status, next_data, delay)
                    if status == 429 or 500 <= status <= 599:
                        if isinstance(next_data, dict) and (
                            str(next_data.get("status", "")).upper() in {"FAILED", "CANCEL"}
                            or next_data.get("errorCode")
                            or next_data.get("errorMessage")
                        ):
                            self._validate(status, next_data)
                        retry_count += 1
                        await self._notify("reconnecting")
                        await self._wait(max(backoff, delay or 0))
                        backoff = min(backoff * 2, t.retry_backoff_max_seconds)
                        continue
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
                try:
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    valid = parsed and parsed.scheme in {"http", "https"} and parsed.hostname
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
                    "downloading", index * 100 / len(urls), {"completed": index, "total": len(urls)}
                )
            await self._notify("succeeded", 100)
            self.remote_status = "SUCCESS"
            return images
