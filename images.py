"""Ordered IMAGE-list conversion without resizing or implicit alpha loss."""

import base64
from io import BytesIO

import numpy as np
import torch
from PIL import Image


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


def encode_images(batches, encoding="base64_png", check_cancel=lambda: None):
    if batches is None:
        return []
    if not isinstance(batches, (list, tuple)):
        raise ValueError("images must be the ComfyUI list of IMAGE batches.")
    if encoding not in {"base64_png", "data_url_png"}:
        raise ValueError("Unsupported image encoding.")
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
                value = base64.b64encode(buffer.getvalue()).decode("ascii")
            except (OSError, ValueError, RuntimeError):
                raise ValueError(f"Could not encode input image {index}.") from None
            encoded.append(("data:image/png;base64," if encoding == "data_url_png" else "") + value)
    return encoded


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
