"""Validated, immutable configuration loaded once per ComfyUI process."""

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Parameter:
    options: tuple[tuple[str, str], ...]
    default: str

    @property
    def values(self):
        return tuple(value for value, _ in self.options)


@dataclass(frozen=True)
class Profile:
    family: str
    parameters: Mapping[str, Parameter]
    max_reference_images: int | None = None
    reference_limit_source: str | None = None


@dataclass(frozen=True)
class Transport:
    poll_interval_seconds: float = 3
    task_timeout_seconds: float | None = None
    connect_timeout_seconds: float = 15
    submit_timeout_seconds: float = 120
    poll_request_timeout_seconds: float = 60
    download_timeout_seconds: float = 120
    retry_backoff_max_seconds: float = 30
    download_retry_limit: int = 3
    image_encoding: str = "base64_png"


@dataclass(frozen=True)
class Config:
    base_url: str
    default_model: str
    transport: Transport
    models: Mapping[str, Profile]
    batch_reference_limit: int = 10

    def profile(self, model: str) -> Profile:
        if not isinstance(model, str) or model not in self.models:
            raise ConfigError("Unknown or disabled model; update the workflow explicitly.")
        return self.models[model]

    def public_catalog(self):
        return {
            "base_url": self.base_url,
            "default_model": self.default_model,
            "batch_reference_limit": self.batch_reference_limit,
            "models": {
                model: {
                    "family": profile.family,
                    "parameters": {
                        name: {
                            "default": param.default,
                            "options": [{"value": value, "label": label} for value, label in param.options],
                        }
                        for name, param in profile.parameters.items()
                    },
                }
                for model, profile in self.models.items()
            },
        }


def _object(value, allowed, required, where):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ConfigError(f"Invalid fields in {where}.")
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ConfigError(f"Expected nonempty text in {where}.")
    return value


def normalize_base_url(value, where="base_url"):
    base = _text(value, where).rstrip("/")
    url = urlsplit(base)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
    ):
        raise ConfigError(f"{where} must be an HTTP(S) host root without credentials, path or query.")
    try:
        url.port
    except ValueError:
        raise ConfigError(f"Invalid {where} port.") from None
    return base


def parse_config(raw) -> Config:
    fields = {
        "schema_version",
        "base_url",
        "default_model",
        "transport",
        "parameter_presets",
        "profiles",
        "model_groups",
    }
    _object(raw, fields | {"batch_reference_limit"}, fields, "configuration")
    local_limit = raw.get("batch_reference_limit", 10)
    if type(local_limit) is not int or not 1 <= local_limit <= 100:
        raise ConfigError("batch_reference_limit must be an integer from 1 to 100 (local safety limit).")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ConfigError("Unsupported schema_version; expected 1.")
    base = normalize_base_url(raw["base_url"])
    network = _object(raw["transport"], Transport.__dataclass_fields__, (), "transport")
    transport = Transport(**network)
    for name in Transport.__dataclass_fields__:
        value = getattr(transport, name)
        if name.endswith("seconds"):
            if name == "task_timeout_seconds" and value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ConfigError(f"{name} must be a positive finite number.")
    if type(transport.download_retry_limit) is not int or not 0 <= transport.download_retry_limit <= 3:
        raise ConfigError("download_retry_limit must be an integer from 0 to 3.")
    if transport.image_encoding not in {"base64_png", "data_url_png"}:
        raise ConfigError("Unsupported image_encoding.")
    presets = raw["parameter_presets"]
    if not isinstance(presets, dict) or not presets:
        raise ConfigError("parameter_presets must be a nonempty object.")
    options = {}
    for name, entries in presets.items():
        _text(name, "preset name")
        if not isinstance(entries, list) or not entries:
            raise ConfigError("Each preset must contain options.")
        pairs = []
        for entry in entries:
            _object(entry, {"value", "label"}, {"value", "label"}, "preset option")
            pairs.append((_text(entry["value"], "option value"), _text(entry["label"], "option label")))
        if len({value for value, _ in pairs}) != len(pairs):
            raise ConfigError("Duplicate option values in preset.")
        options[name] = tuple(pairs)
    raw_profiles = raw["profiles"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ConfigError("profiles must be a nonempty object.")
    profiles = {}
    family_fields = {"nano_banana": {"aspectRatio", "imageSize"}, "gpt_image": {"aspectRatio", "quality"}}
    for name, profile in raw_profiles.items():
        _text(name, "profile name")
        _object(profile, {"family", "parameters", "max_reference_images", "reference_limit_source"},
                {"family", "parameters"}, "profile")
        limit = profile.get("max_reference_images")
        source = profile.get("reference_limit_source")
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise ConfigError("max_reference_images must be a positive integer.")
            source = _text(source, "official reference_limit_source")
        elif source is not None:
            raise ConfigError("reference_limit_source requires max_reference_images.")
        family = _text(profile["family"], "family")
        if family not in family_fields:
            raise ConfigError("Unsupported profile family.")
        params = _object(
            profile["parameters"], family_fields[family], family_fields[family], "profile parameters"
        )
        parsed = {}
        for field, rule in params.items():
            _object(rule, {"preset", "default"}, {"preset", "default"}, "parameter rule")
            preset = _text(rule["preset"], "preset reference")
            default = _text(rule["default"], "parameter default")
            if preset not in options or default not in {v for v, _ in options[preset]}:
                raise ConfigError("Missing preset or invalid parameter default.")
            parsed[field] = Parameter(options[preset], default)
        profiles[name] = Profile(family, MappingProxyType(parsed), limit, source)
    groups = raw["model_groups"]
    if not isinstance(groups, list) or not groups:
        raise ConfigError("model_groups must be a nonempty list.")
    models, seen = {}, set()
    for group in groups:
        _object(group, {"enabled", "models", "profile"}, {"enabled", "models", "profile"}, "model group")
        profile = _text(group["profile"], "profile reference")
        if type(group["enabled"]) is not bool or profile not in profiles:
            raise ConfigError("Invalid enabled flag or missing profile.")
        if not isinstance(group["models"], list) or not group["models"]:
            raise ConfigError("Each model group must contain models.")
        for model in group["models"]:
            _text(model, "model")
            if model in seen:
                raise ConfigError("Duplicate model in configuration.")
            seen.add(model)
            if group["enabled"]:
                models[model] = profiles[profile]
    default = _text(raw["default_model"], "default_model")
    if default not in models:
        raise ConfigError("default_model must be enabled.")
    return Config(base, default, transport, MappingProxyType(models), local_limit)


def load_config(directory: Path | None = None) -> Config:
    directory = directory or Path(__file__).parent
    path = directory / "grsai_config.json"
    if not path.exists():
        path = directory / "grsai_config.example.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError(f"Cannot read valid JSON from {path.name}.") from None
    return parse_config(raw)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
