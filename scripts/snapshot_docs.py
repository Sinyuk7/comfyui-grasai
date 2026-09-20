#!/usr/bin/env python3
"""Save dated, byte-preserving documentation snapshots and a source manifest."""

import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.request


SOURCES = [
    ("grsai/nano-banana.md", "https://qmy27nhsd9.apifox.cn/452392911e0.md"),
    ("grsai/gpt-image.md", "https://qmy27nhsd9.apifox.cn/452409160e0.md"),
    ("grsai/result.md", "https://qmy27nhsd9.apifox.cn/452409577e0.md"),
    ("comfyui/v3-migration.md", "https://docs.comfy.org/custom-nodes/v3_migration.md"),
    ("comfyui/data-lists.md", "https://docs.comfy.org/custom-nodes/backend/lists.md"),
    ("comfyui/images-and-masks.md", "https://docs.comfy.org/custom-nodes/backend/images_and_masks.md"),
    ("comfyui/node-properties.md", "https://docs.comfy.org/custom-nodes/backend/server_overview.md"),
    ("comfyui/javascript-extensions.md", "https://docs.comfy.org/custom-nodes/js/javascript_overview.md"),
]


def fetch(source):
    relative, url = source
    request = urllib.request.Request(url, headers={"User-Agent": "comfyui-grsai-docs/0.1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        resolved_url = response.url
    text = body.decode("utf-8")
    if "<html" in text[:1000].lower() or not text.strip():
        raise ValueError(f"Expected non-empty Markdown from {url}")
    if relative.startswith("grsai/") and "openapi: 3.0.1" not in text:
        raise ValueError(f"Missing OpenAPI specification: {url}")
    return relative, body, {
        "path": relative,
        "url": url,
        "resolved_url": resolved_url,
        "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "content_type": content_type,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def main():
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    root = Path(__file__).resolve().parents[1] / "docs" / "references" / "snapshots" / timestamp
    # Fetch all sources before writing, so a failed fetch does not create a partial snapshot.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(fetch, SOURCES))
    root.mkdir(parents=True, exist_ok=False)
    for relative, body, _ in results:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    manifest = {"snapshot": timestamp, "sources": [entry for _, _, entry in results]}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(root)
    print(f"Saved {len(results)} sources; manifest contains original URLs and SHA-256 hashes.")


if __name__ == "__main__":
    main()
