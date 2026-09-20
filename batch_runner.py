"""Bounded task lifecycles with submit-once transport and incremental persistence."""

import asyncio
import hashlib
import logging
import sys
import time

import aiohttp

from .batch_plan import validate_options
from .batch_storage import BatchStore, StorageError
from .client import GrsaiClient, cancellable
from .diagnostics import log_event, new_run_id
from .errors import GrsaiError, clean_message
from .images import encode_file_payloads, encode_image_files, validate_reference_files
from .request_builder import build_request, build_runninghub_request, normalize_key
from .runninghub_client import RunningHubClient
from .runninghub_config import get_runninghub_catalog


class BatchRunner:
    def __init__(self, config, key, plan, model, parameters, concurrency, prefix, output_root,
                 check_cancel=lambda: None, progress=None, run_id=None, node_id=None, provider="grsai"):
        self.config = config
        self.key = normalize_key(key)
        validate_options(concurrency, prefix, self.key)
        if provider not in {"grsai", "runninghub"}:
            raise ValueError("Unsupported image API provider.")
        if provider == "grsai":
            for prompt in plan.prompts:
                build_request(model, prompt, parameters, ["pending"], config)
        else:
            catalog = get_runninghub_catalog()
            for prompt in plan.prompts:
                build_runninghub_request(model, prompt, parameters, ["pending"], catalog)
        self.plan, self.model, self.parameters = plan, model, parameters
        self.provider = provider
        self.runninghub_profile = get_runninghub_catalog().profile(model) if provider == "runninghub" else None
        self.concurrency, self.check_cancel, self.progress = concurrency, check_cancel, progress
        self.store = BatchStore(
            output_root,
            plan,
            model,
            parameters,
            concurrency,
            prefix,
            self.key,
            provider,
            self.runninghub_profile.endpoint if self.runninghub_profile else "/v1/api/generate",
        )
        self.run_id = run_id or new_run_id()
        self.node_id = node_id
        self.submitted = False
        self.cooldown_until = 0
        self.fatal = None
        self.encoded = {}
        self.uploaded = {}
        self.upload_lock = asyncio.Lock()
        self.remaining = {base: len(plan.prompts) for base in range(1, plan.base_count + 1)}
        self.active = {}

    async def _emit(self, stage):
        if self.progress:
            tasks = self.store.state["tasks"]
            success = sum(t["status"] == "succeeded" for t in tasks)
            failed = sum(t["status"] in {"failed", "partial", "submission_unknown"} for t in tasks)
            active = [self.active[index] for index in sorted(self.active)]
            reported = [item["progress"] for item in active
                        if item.get("stage") == "running"
                        and type(item.get("progress")) in (int, float)
                        and 0 <= item["progress"] <= 100]
            overall_progress = ((success + failed) + sum(value / 100 for value in reported)) * 100 / self.plan.total
            try:
                await self.progress({"stage": stage, "base_count": self.plan.base_count,
                                     "prompt_count": len(self.plan.prompts), "total": self.plan.total,
                                     "reference_count": len(self.plan.columns), "model": self.model,
                                     "concurrency": self.concurrency, "completed": success + failed,
                                     "overall_progress": min(100, overall_progress),
                                     "success": success, "failed": failed, "running": len(active),
                                     "active": active,
                                     "directory": str(self.store.path).replace(self.key, "[redacted]"),
                                     "manifest": str(self.store.manifest).replace(self.key, "[redacted]")})
            except Exception:
                pass  # Display failure is never a persistence or generation failure.

    async def _pressure(self, delay):
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay)

    async def _wait_to_submit(self):
        while True:
            self.check_cancel()
            remaining = self.cooldown_until - time.monotonic()
            if self.fatal or remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.2))

    def _images(self, base):
        images = []
        for col, column in enumerate(self.plan.columns):
            index = self.plan.input_index(col, base)
            cache_key = col, index
            if cache_key not in self.encoded:
                self.encoded[cache_key] = encode_image_files(
                    [column.images[index]], self.check_cancel, self.provider != "runninghub"
                )[0]
            images.append(self.encoded[cache_key])
        return images

    async def _runninghub_urls(self, client, files):
        urls = []
        for index, content in enumerate(files, 1):
            digest = hashlib.sha256(content).hexdigest()
            async with self.upload_lock:
                if digest not in self.uploaded:
                    api = client.sessions[0]
                    self.uploaded[digest] = await client.upload_image(api, content, index)
                urls.append(self.uploaded[digest])
        return urls

    def _release(self, base):
        self.remaining[base] -= 1
        if not self.remaining[base]:
            for col, column in enumerate(self.plan.columns):
                if len(column.images) > 1:
                    self.encoded.pop((col, base - 1), None)

    async def _task(self, spec, sessions):
        client = None

        async def task_progress(stage, value, _task_id, details):
            self.active[spec.task_index] = {
                "task_index": spec.task_index,
                "stage": stage,
                "progress": value,
                **details,
            }
            await self._emit("running")

        async def accepted(task_id, remote_status):
            await self.store.update(spec.task_index, remote_task_id=task_id, remote_status=remote_status,
                                    submitted=True)

        async def result(image, index, count):
            await self.store.save(spec.task_index, image, index, count)

        try:
            files = self._images(spec.base_index)
            validate_reference_files(files, self.provider != "runninghub")
            # Encoding is synchronous. Re-check pressure and fatal state before committing a POST.
            await self._wait_to_submit()
            if self.fatal:
                return
            self.check_cancel()
            await self.store.update(spec.task_index, status="running")
            common = {
                "accepted": accepted,
                "result": result,
                "pressure": self._pressure,
                "sessions": sessions,
                "log_context": {"run_id": self.run_id, "node_id": self.node_id,
                                "batch_id": self.store.path.name, "task_index": spec.task_index},
            }
            if self.provider == "runninghub":
                build_runninghub_request(
                    self.model,
                    self.plan.prompts[spec.prompt_index - 1],
                    self.parameters,
                    ["pending"] * len(files),
                    get_runninghub_catalog(),
                )
                client = RunningHubClient(
                    self.config, self.key, self.check_cancel, task_progress,
                    endpoint=self.runninghub_profile.endpoint, **common
                )
                urls = await self._runninghub_urls(client, files)
                request = build_runninghub_request(
                    self.model,
                    self.plan.prompts[spec.prompt_index - 1],
                    self.parameters,
                    urls,
                    get_runninghub_catalog(),
                )
            else:
                encoded = encode_file_payloads(files, self.config.transport.image_encoding)
                request = build_request(
                    self.model, self.plan.prompts[spec.prompt_index - 1], self.parameters, encoded, self.config
                )
                client = GrsaiClient(
                    self.config, self.key, self.check_cancel, task_progress, **common
                )
            await client.generate(request)
            await self.store.update(spec.task_index, status="succeeded", remote_status=client.remote_status)
        except GrsaiError as exc:
            error = clean_message(str(exc), (self.key, *self.plan.prompts))
            if client and client.http_status in {401, 403}:
                self.fatal = error
            outputs = self.store.state["tasks"][spec.task_index - 1]["outputs"]
            state = "partial" if outputs else (
                "submission_unknown" if client and client.submission_unknown else "failed"
            )
            await self.store.update(spec.task_index, status=state, error=error,
                                    remote_status=client.remote_status if client else None)
            log_event(
                "batch.task_failed",
                level=logging.ERROR,
                run_id=self.run_id,
                node_id=self.node_id,
                batch_id=self.store.path.name,
                task_index=spec.task_index,
                task_id=client.task_id if client else None,
                status=state,
                error=error,
            )
        except (StorageError, ValueError) as exc:
            self.fatal = clean_message(str(exc), (self.key, *self.plan.prompts))
            raise
        finally:
            if client:
                self.submitted |= client.submitted
                unwinding = sys.exc_info()[0] is not None
                try:
                    await self.store.update(spec.task_index, submitted=client.submitted,
                                            remote_status=client.remote_status, remote_task_id=client.task_id)
                except StorageError:
                    if not unwinding:
                        raise
            self.active.pop(spec.task_index, None)
            self._release(spec.base_index)
        await self._emit("running")

    async def _execute(self):
        iterator = iter(self.plan.tasks())

        async def worker(sessions):
            while not self.fatal:
                await self._wait_to_submit()
                if self.fatal:
                    break
                spec = next(iterator, None)
                if spec is None:
                    break
                await self._task(spec, sessions)

        async with aiohttp.ClientSession() as api, aiohttp.ClientSession() as cdn:
            workers = [asyncio.create_task(worker((api, cdn)))
                       for _ in range(min(self.concurrency, self.plan.total))]
            try:
                await asyncio.gather(*workers)
            finally:
                for task in workers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

    async def run(self):
        started = time.monotonic()
        log_event(
            "batch.started",
            run_id=self.run_id,
            node_id=self.node_id,
            batch_id=self.store.path.name,
            model=self.model,
            tasks=self.plan.total,
            concurrency=self.concurrency,
        )
        await self._emit("planned")
        try:
            await cancellable(self._execute(), self.check_cancel)
        except BaseException as exc:
            interrupted = isinstance(exc, asyncio.CancelledError)
            try:
                self.check_cancel()
            except BaseException:
                interrupted = True
            status = "interrupted" if interrupted else "failed"
            error = clean_message(str(exc), (self.key, *self.plan.prompts))
            try:
                await self.store.finish(status, error, interrupted_tasks=True)
            except StorageError:
                pass
            await self._emit(status)
            log_event(
                "batch.interrupted" if interrupted else "batch.failed",
                level=logging.WARNING if interrupted else logging.ERROR,
                run_id=self.run_id,
                node_id=self.node_id,
                batch_id=self.store.path.name,
                error=error,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        finally:
            self.encoded.clear()
            self.uploaded.clear()
        tasks = self.store.state["tasks"]
        status = "succeeded" if all(t["status"] == "succeeded" for t in tasks) else (
            "partial" if self.store.images else "failed"
        )
        images = await self.store.finish(status, self.fatal)
        await self._emit(status)
        summary = self.store.state["summary"]
        log_event(
            "batch.completed",
            level=logging.INFO if status == "succeeded" else logging.WARNING,
            run_id=self.run_id,
            node_id=self.node_id,
            batch_id=self.store.path.name,
            status=status,
            succeeded=summary["succeeded_tasks"],
            failed=summary["failed_tasks"],
            outputs=summary["saved_images"],
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return images, str(self.store.manifest)
