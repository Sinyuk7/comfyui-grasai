"""Single-owner in-memory manifest and atomic files, without background writers."""

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image


class StorageError(RuntimeError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sanitized(value, key):
    if isinstance(value, str):
        return value.replace(key, "[redacted]")
    if isinstance(value, dict):
        return {k: sanitized(v, key) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitized(v, key) for v in value]
    return value


class BatchStore:
    def __init__(self, output_root, plan, model, parameters, concurrency, prefix, api_key):
        self.prefix = prefix
        self.api_key = api_key
        self.lock = asyncio.Lock()
        self.images = {}
        self.path = Path(output_root) / "grsai" / (
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:12]
        )
        try:
            self.path.mkdir(parents=True, exist_ok=False)
        except OSError:
            raise StorageError("Cannot create batch output directory; no generation was submitted.") from None
        bases = []
        for base in range(1, plan.base_count + 1):
            mapping = []
            for col, column in enumerate(plan.columns):
                index = plan.input_index(col, base)
                mapping.append({"reference": col + 1, "input_index": index + 1,
                                "reused": len(column.images) == 1,
                                "source": asdict(column.sources[index])})
            bases.append({"base_index": base, "references": mapping})
        self.state = {
            "schema_version": 1, "batch_id": self.path.name, "started_at": timestamp(),
            "finished_at": None, "status": "planned", "model": model, "parameters": dict(parameters),
            "reference_count": len(plan.columns), "base_count": plan.base_count,
            "prompt_count": len(plan.prompts), "total_tasks": plan.total,
            "prompt_source": plan.prompt_source, "max_concurrency": concurrency,
            "output_prefix": prefix, "bases": bases,
            "prompts": [{"prompt_index": i, "sha256": hashlib.sha256(p.encode()).hexdigest()}
                        for i, p in enumerate(plan.prompts, 1)],
            "tasks": [{**asdict(task), "status": "pending", "remote_task_id": None,
                       "remote_status": None, "submitted": False,
                       "error": None, "outputs": []} for task in plan.tasks()],
        }
        self._write()

    @property
    def manifest(self):
        return self.path / "manifest.json"

    def _atomic(self, destination, write):
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".grsai-", dir=self.path)
            with os.fdopen(fd, "wb") as stream:
                write(stream)
                stream.flush()
            os.replace(temporary, destination)
        except (OSError, ValueError):
            raise StorageError("Batch storage unavailable; already saved files were retained.") from None
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def _write(self):
        data = json.dumps(sanitized(self.state, self.api_key), ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic(self.manifest, lambda stream: stream.write(data))

    async def update(self, index, **changes):
        async with self.lock:
            self.state["tasks"][index - 1].update(changes)
            self._write()

    async def save(self, task_index, image, result_index, result_count):
        async with self.lock:
            suffix = f"_{result_index:02d}" if result_count > 1 else ""
            name = f"{self.prefix}_{task_index:03d}{suffix}.png"
            pixels = (image[0].detach().cpu().float().numpy() * 255).round().astype("uint8")
            self._atomic(self.path / name, lambda stream: Image.fromarray(pixels).save(stream, format="PNG"))
            self.images[(task_index, result_index)] = image
            self.state["tasks"][task_index - 1]["outputs"].append({
                "result_index": result_index, "file": name, "flat_output_index": None,
            })
            self._write()

    async def finish(self, status, error=None, interrupted_tasks=False):
        async with self.lock:
            self.state.update(status=status, finished_at=timestamp(), error=error)
            if interrupted_tasks:
                for task in self.state["tasks"]:
                    if task["status"] == "running":
                        task["status"] = "interrupted"
                        if task["submitted"] and not task["remote_task_id"]:
                            task["submission_state"] = "unknown"
            if not interrupted_tasks and status != "interrupted":
                for flat, (task, result) in enumerate(sorted(self.images)):
                    for output in self.state["tasks"][task - 1]["outputs"]:
                        if output["result_index"] == result:
                            output["flat_output_index"] = flat
            tasks = self.state["tasks"]
            self.state["summary"] = {
                "succeeded_tasks": sum(t["status"] == "succeeded" for t in tasks),
                "failed_tasks": sum(t["status"] in {"failed", "partial", "submission_unknown"} for t in tasks),
                "pending_tasks": sum(t["status"] == "pending" for t in tasks),
                "interrupted_tasks": sum(t["status"] == "interrupted" for t in tasks),
                "saved_images": len(self.images),
            }
            self._write()
        return [self.images[key] for key in sorted(self.images)]
