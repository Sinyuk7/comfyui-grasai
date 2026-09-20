"""Ordered folder snapshots and explicit source metadata; no host or network imports."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import torch

from .images import decode_image, split_images


@dataclass(frozen=True)
class Source:
    filename: str
    path: str | None
    sequence_index: int
    sha256: str


@dataclass(frozen=True)
class ReferenceSet:
    images: tuple[torch.Tensor, ...]
    sources: tuple[Source, ...]


def natural_key(name):
    parts = tuple((1, int(p)) if p.isascii() and p.isdigit() else (0, p.casefold())
                  for p in re.split(r"([0-9]+)", name))
    return parts, name.casefold(), name


def load_folder(folder, check_cancel=lambda: None):
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("Folder must be a nonempty server-side directory path.")
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Folder is not an accessible directory on the ComfyUI server.")
    try:
        paths = sorted(
            (p for p in root.iterdir() if not p.name.startswith(".") and p.is_file()
             and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
            key=lambda p: natural_key(p.name),
        )
    except OSError:
        raise ValueError("Cannot read the server-side folder.") from None
    if not paths:
        raise ValueError("Folder contains no supported images.")
    images, sources = [], []
    for index, path in enumerate(paths, 1):
        check_cancel()
        try:
            data = path.read_bytes()
            image = decode_image(data)
        except (OSError, ValueError):
            raise ValueError(f"Cannot load folder image {index}; no images were skipped.") from None
        images.append(image)
        sources.append(Source(path.name, str(path), index, hashlib.sha256(data).hexdigest()))
    return ReferenceSet(tuple(images), tuple(sources))


def normalize_reference(values, check_cancel=lambda: None):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("A connected Reference must contain a nonempty IMAGE list.")
    images, sources = [], []
    for value in values:
        check_cancel()
        if isinstance(value, ReferenceSet):
            if not value.images or len(value.images) != len(value.sources):
                raise ValueError("Reference images and source metadata must align.")
            singles = split_images(value.images, check_cancel)
            if len(singles) != len(value.sources) or any(
                not isinstance(s, Source) or not isinstance(s.filename, str)
                or (s.path is not None and not isinstance(s.path, str))
                or type(s.sequence_index) is not int or s.sequence_index < 1
                or not isinstance(s.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", s.sha256)
                for s in value.sources
            ):
                raise ValueError("Invalid Reference source metadata.")
            images.extend(singles)
            sources.extend(value.sources)
        else:
            for single in split_images([value], check_cancel):
                digest = hashlib.sha256(single.detach().cpu().float().contiguous().numpy().tobytes()).hexdigest()
                images.append(single)
                sources.append(Source("unknown", None, len(images), digest))
    return ReferenceSet(tuple(images), tuple(sources))
