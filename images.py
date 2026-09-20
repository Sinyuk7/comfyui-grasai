"""Ordered IMAGE-list conversion without resizing or implicit alpha loss."""

import base64
from io import BytesIO

import numpy as np
import torch
from PIL import Image


MAX_REFERENCE_IMAGES = 10
MAX_REFERENCE_IMAGE_BYTES = 10_000_000
MAX_REFERENCE_TOTAL_BYTES = 50_000_000


def validate_reference_files(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_REFERENCE_IMAGES:
        raise ValueError("A request requires 1 to 10 reference images.")
    for index, value in enumerate(files, 1):
        if not isinstance(value, bytes) or not value:
            raise ValueError(f"Input image {index} did not encode to PNG bytes.")
        if len(value) > MAX_REFERENCE_IMAGE_BYTES:
            raise ValueError(f"Input image {index} exceeds the 10 MB encoded PNG limit.")
    if sum(map(len, files)) > MAX_REFERENCE_TOTAL_BYTES:
        raise ValueError("Reference images exceed the 50 MB total encoded PNG limit.")
    return files


def split_images(batches, check_cancel=lambda: None):
    """Validate and return single-image views, preserving list then batch order."""
    if not isinstance(batches, (list, tuple)):
        raise ValueError("images must be the ComfyUI list of IMAGE batches.")
    images = []
    for batch_index, batch in enumerate(batches, 1):
        check_cancel()
        if (
            not isinstance(batch, torch.Tensor) or batch.ndim != 4
            or batch.shape[-1] != 3 or min(batch.shape) < 1
        ):
            raise ValueError(f"Input batch {batch_index} must have shape [B,H,W,3].")
        for tensor in batch:
            check_cancel()
            if (
                not tensor.is_floating_point() or not torch.isfinite(tensor).all()
                or tensor.min() < 0 or tensor.max() > 1
            ):
                raise ValueError(f"Input image {len(images) + 1} must contain finite floats in [0,1].")
            images.append(tensor.unsqueeze(0))
    return images


def encode_image_files(batches, check_cancel=lambda: None):
    if batches is None:
        return []
    if not isinstance(batches, (list, tuple)):
        raise ValueError("images must be the ComfyUI list of IMAGE batches.")
    encoded = []
    for batch_index, batch in enumerate(batches, 1):
        check_cancel()
        if (
            not isinstance(batch, torch.Tensor)
            or batch.ndim != 4
            or batch.shape[-1] != 3
            or min(batch.shape) < 1
        ):
            raise ValueError(
                f"Input batch {batch_index} (image {len(encoded) + 1}) must have shape [B,H,W,3]."
            )
        for tensor in batch:
            check_cancel()
            index = len(encoded) + 1
            if (
                not tensor.is_floating_point()
                or not torch.isfinite(tensor).all()
                or tensor.min() < 0
                or tensor.max() > 1
            ):
                raise ValueError(f"Input image {index} must contain finite floats in [0,1].")
            try:
                pixels = (
                    (tensor.detach().to(device="cpu", dtype=torch.float32).numpy() * 255)
                    .round()
                    .astype(np.uint8)
                )
                buffer = BytesIO()
                Image.fromarray(pixels).save(buffer, format="PNG")
                value = buffer.getvalue()
            except (OSError, ValueError, RuntimeError):
                raise ValueError(f"Could not encode input image {index}.") from None
            if len(value) > MAX_REFERENCE_IMAGE_BYTES:
                raise ValueError(f"Input image {index} exceeds the 10 MB encoded PNG limit.")
            encoded.append(value)
            if len(encoded) > MAX_REFERENCE_IMAGES:
                raise ValueError("A request supports at most 10 reference images.")
            if sum(map(len, encoded)) > MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError("Reference images exceed the 50 MB total encoded PNG limit.")
    return validate_reference_files(encoded) if encoded else []


def encode_images(batches, encoding="base64_png", check_cancel=lambda: None):
    if encoding not in {"base64_png", "data_url_png"}:
        raise ValueError("Unsupported image encoding.")
    return encode_file_payloads(encode_image_files(batches, check_cancel), encoding)


def encode_file_payloads(files, encoding="base64_png"):
    if encoding not in {"base64_png", "data_url_png"}:
        raise ValueError("Unsupported image encoding.")
    prefix = "data:image/png;base64," if encoding == "data_url_png" else ""
    return [prefix + base64.b64encode(value).decode("ascii") for value in files]


def decode_image(data: bytes):
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated/multiframe output is not supported.")
            if "A" in image.getbands() or "transparency" in image.info:
                if image.convert("RGBA").getchannel("A").getextrema() != (255, 255):
                    raise ValueError(
                        "Output has nonopaque alpha; RGB IMAGE output cannot preserve transparency."
                    )
            array = np.array(image.convert("RGB"), dtype=np.float32) / 255.0
    except (OSError, Image.DecompressionBombError):
        raise ValueError("Downloaded result is not a decodable supported image.") from None
    return torch.from_numpy(array).unsqueeze(0)
