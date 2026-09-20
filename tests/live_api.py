"""Explicit opt-in paid smoke test using the production request/image/client pipeline.

Normal pytest discovery never runs this script. Credentials enter through stdin
or GRSAI_API_KEY and are never included in reports, command arguments or PNGs.
"""

# Package bootstrap is required because this custom-node directory is not installed.
# ruff: noqa: E402

import argparse
import asyncio
import hashlib
import getpass
import importlib.util
import json
import os
import sys
import time
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
import torch

ROOT = Path(__file__).resolve().parents[1]
if "grsai" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "grsai", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["grsai"] = package
    spec.loader.exec_module(package)

from grsai.client import GrsaiClient
from grsai.config import load_config
from grsai.errors import clean_message
from grsai.images import encode_images
from grsai.request_builder import build_request, normalize_key


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_case(case_path, image_dir):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    if len(case["images"]) != 3:
        raise ValueError("The live outfit-edit test requires exactly three ordered references.")
    batches, manifest = [], []
    for index, name in enumerate(case["images"], 1):
        path = (image_dir / name).resolve()
        if not path.is_relative_to(image_dir.resolve()):
            raise ValueError("Reference paths must stay within the image directory.")
        source_bytes = path.read_bytes()
        with Image.open(BytesIO(source_bytes)) as original:
            image = ImageOps.exif_transpose(original)
            if "A" in image.getbands() and image.getchannel("A").getextrema() != (255, 255):
                raise ValueError(f"Reference {index} has transparency; define explicit handling first.")
            rgb = image.convert("RGB")
            batches.append(torch.from_numpy(np.array(rgb, dtype=np.float32) / 255).unsqueeze(0))
            manifest.append(
                {
                    "index": index,
                    "file": name,
                    "width": rgb.width,
                    "height": rgb.height,
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                }
            )
    return case, batches, manifest


def write_report(path, report, key=""):
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if key and key in text:
        raise RuntimeError("Refusing to write a report containing credentials.")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(path)


class ObservedClient(GrsaiClient):
    def __init__(self, *args, record, flush, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = record
        self.flush = flush

    async def _json(self, session, method, path, timeout, **kwargs):
        event = {"method": method, "path": path, "started_at": utc_now()}
        self.record["requests"].append(event)
        self.flush()
        try:
            status, data, delay = await super()._json(session, method, path, timeout, **kwargs)
            event["http_status"] = status
            # Record structural evidence, never raw responses, URLs or reflected request bodies.
            event["response_is_object"] = isinstance(data, dict)
            if isinstance(data, dict):
                event["response_fields"] = sorted(str(k) for k in data)
                state = data.get("status")
                event["upstream_status"] = (
                    state if state in ("running", "succeeded", "failed", "violation") else "unknown"
                )
                event["results_count"] = (
                    len(data["results"]) if isinstance(data.get("results"), list) else None
                )
            return status, data, delay
        finally:
            event["finished_at"] = utc_now()
            self.flush()


async def run(args, key=""):
    config = load_config()
    case, batches, manifest = load_case(args.case, args.images)
    selected = args.models or list(case["models"])
    if len(selected) != len(set(selected)) or any(model not in case["models"] for model in selected):
        raise ValueError("Select each fixture model at most once.")
    encoded = encode_images(batches, config.transport.image_encoding)
    requests = {
        model: build_request(model, case["prompt"], case["models"][model], encoded, config)
        for model in selected
    }
    output = args.output or ROOT / "tests" / "outputs" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = {
        "case": case["name"],
        "started_at": utc_now(),
        "base_url": config.base_url,
        "image_encoding": config.transport.image_encoding,
        "inputs": manifest,
        "prompt": case["prompt"],
        "prompt_zh": case["prompt_zh"],
        "prepare_only": args.prepare_only,
        "runs": [],
    }

    def flush():
        write_report(report_path, report, key)

    flush()
    print(f"REPORT {report_path}", flush=True)
    for model, body in requests.items():
        record = {
            "model": model,
            "parameters": case["models"][model],
            "input_image_count": len(encoded),
            "status": "prepared",
            "requests": [],
            "outputs": [],
        }
        report["runs"].append(record)
        flush()
        if args.prepare_only:
            continue
        started = time.monotonic()
        last = None

        async def progress(stage, value, task_id):
            nonlocal last
            record.update(stage=stage, task_id=task_id)
            marker = (stage, value, task_id)
            if marker != last:
                print(
                    json.dumps({"model": model, "stage": stage, "progress": value, "task_id": task_id}),
                    flush=True,
                )
                last = marker
            flush()

        client = ObservedClient(config, key, progress=progress, record=record, flush=flush)
        record["status"] = "executing"
        record["started_at"] = utc_now()
        flush()
        try:
            images = await client.generate(body)
            for index, tensor in enumerate(images, 1):
                pixels = (tensor[0].numpy() * 255).round().astype(np.uint8)
                path = output / f"{model}-{index}.png"
                Image.fromarray(pixels).save(path, format="PNG")
                record["outputs"].append(
                    {
                        "file": path.name,
                        "width": pixels.shape[1],
                        "height": pixels.shape[0],
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            record["status"] = "succeeded"
        except asyncio.CancelledError:
            record["status"] = "interrupted"
            raise
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = clean_message(str(exc), (key, case["prompt"], *encoded))
            record["error_type"] = type(exc).__name__
        finally:
            record["task_id"] = client.task_id
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["finished_at"] = utc_now()
            record["generate_post_count"] = sum(e["method"] == "POST" for e in record["requests"])
            flush()
        print(
            json.dumps(
                {
                    "model": model,
                    "status": record["status"],
                    "task_id": client.task_id,
                    "elapsed_seconds": record["elapsed_seconds"],
                    "outputs": record["outputs"],
                    "error": record.get("error"),
                }
            ),
            flush=True,
        )
    report["finished_at"] = utc_now()
    flush()
    return 0 if all(r["status"] in {"prepared", "succeeded"} for r in report["runs"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="Explicit authorization for one billable POST per selected model",
    )
    parser.add_argument(
        "--prepare-only", action="store_true", help="Validate and encode locally; make no HTTP requests"
    )
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read API key from one stdin line instead of the environment",
    )
    parser.add_argument("--case", type=Path, default=ROOT / "tests" / "live_case.json")
    parser.add_argument("--images", type=Path, default=ROOT / "tests" / "images")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--models", nargs="+", choices=["nano-banana-2", "gpt-image-2.5"])
    args = parser.parse_args()
    if not args.prepare_only and not args.allow_paid:
        parser.error("Real requests require --allow-paid; use --prepare-only for offline validation.")
    key = ""
    if not args.prepare_only:
        if args.api_key_stdin:
            value = (
                getpass.getpass("GRSAI API key (not echoed): ")
                if sys.stdin.isatty()
                else sys.stdin.readline()
            )
        else:
            value = os.environ.get("GRSAI_API_KEY")
        key = normalize_key(value)
    return asyncio.run(run(args, key))


if __name__ == "__main__":
    raise SystemExit(main())
