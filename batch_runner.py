"""Bounded task lifecycles with submit-once transport and incremental persistence."""

import asyncio
import sys
import time

import aiohttp

from .batch_plan import validate_options
from .batch_storage import BatchStore, StorageError
from .client import GrsaiClient, cancellable
from .errors import GrsaiError, clean_message
from .images import encode_images
from .request_builder import build_request, normalize_key


class BatchRunner:
    def __init__(self, config, key, plan, model, parameters, concurrency, prefix, output_root,
                 check_cancel=lambda: None, progress=None):
        self.config = config
        self.key = normalize_key(key)
        validate_options(concurrency, prefix, self.key)
        for prompt in plan.prompts:
            build_request(model, prompt, parameters, [], config)
        self.plan, self.model, self.parameters = plan, model, parameters
        self.concurrency, self.check_cancel, self.progress = concurrency, check_cancel, progress
        self.store = BatchStore(output_root, plan, model, parameters, concurrency, prefix, self.key)
        self.submitted = False
        self.cooldown_until = 0
        self.fatal = None
        self.encoded = {}
        self.remaining = {base: len(plan.prompts) for base in range(1, plan.base_count + 1)}

    async def _emit(self, stage):
        if self.progress:
            tasks = self.store.state["tasks"]
            success = sum(t["status"] == "succeeded" for t in tasks)
            failed = sum(t["status"] in {"failed", "partial", "submission_unknown"} for t in tasks)
            try:
                await self.progress({"stage": stage, "base_count": self.plan.base_count,
                                     "prompt_count": len(self.plan.prompts), "total": self.plan.total,
                                     "reference_count": len(self.plan.columns), "model": self.model,
                                     "concurrency": self.concurrency, "completed": success + failed,
                                     "success": success, "failed": failed,
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
                self.encoded[cache_key] = encode_images(
                    [column.images[index]], self.config.transport.image_encoding, self.check_cancel
                )[0]
            images.append(self.encoded[cache_key])
        return images

    def _release(self, base):
        self.remaining[base] -= 1
        if not self.remaining[base]:
            for col, column in enumerate(self.plan.columns):
                if len(column.images) > 1:
                    self.encoded.pop((col, base - 1), None)

    async def _task(self, spec, sessions):
        client = None

        async def accepted(task_id, remote_status):
            await self.store.update(spec.task_index, remote_task_id=task_id, remote_status=remote_status,
                                    submitted=True)

        async def result(image, index, count):
            await self.store.save(spec.task_index, image, index, count)

        try:
            request = build_request(self.model, self.plan.prompts[spec.prompt_index - 1], self.parameters,
                                    self._images(spec.base_index), self.config)
            # Encoding is synchronous. Re-check pressure and fatal state before committing a POST.
            await self._wait_to_submit()
            if self.fatal:
                return
            self.check_cancel()
            await self.store.update(spec.task_index, status="running")
            client = GrsaiClient(self.config, self.key, self.check_cancel, accepted=accepted,
                                 result=result, pressure=self._pressure, sessions=sessions)
            await client.generate(request)
            await self.store.update(spec.task_index, status="succeeded", remote_status=client.remote_status)
        except GrsaiError as exc:
            error = clean_message(str(exc), (self.key, *self.plan.prompts))
            if client and client.http_status in {401, 403}:
                self.fatal = error
            outputs = self.store.state["tasks"][spec.task_index - 1]["outputs"]
            state = "partial" if outputs else (
                "submission_unknown" if client and client.submitted and not client.task_id else "failed"
            )
            await self.store.update(spec.task_index, status=state, error=error,
                                    remote_status=client.remote_status if client else None)
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
            raise
        finally:
            self.encoded.clear()
        tasks = self.store.state["tasks"]
        status = "succeeded" if all(t["status"] == "succeeded" for t in tasks) else (
            "partial" if self.store.images else "failed"
        )
        images = await self.store.finish(status, self.fatal)
        await self._emit(status)
        return images, str(self.store.manifest)
