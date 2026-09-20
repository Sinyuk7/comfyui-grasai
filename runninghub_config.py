"""Validated local catalog for the curated RunningHub image-editing models."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import ConfigError, Parameter, normalize_base_url


@dataclass(frozen=True)
class RunningHubModel:
    label: str
    channel: str
    endpoint: str
    parameters: Mapping[str, Parameter]
    fixed: Mapping[str, str]
    family: str = "runninghub"
    max_reference_images: int = 10
    reference_limit_source: str = "local shared provider limit"


@dataclass(frozen=True)
class RunningHubCatalog:
    base_url: str
    default_model: str
    models: Mapping[str, RunningHubModel]

    def profile(self, model):
        if not isinstance(model, str) or model not in self.models:
            raise ConfigError("Unknown RunningHub model; update the workflow explicitly.")
        return self.models[model]

    def public_catalog(self):
        return {
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models": {
                model: {
                    "label": profile.label,
                    "family": profile.family,
                    "parameters": {
                        name: {
                            "default": parameter.default,
                            "options": [{"value": value, "label": label} for value, label in parameter.options],
                        }
                        for name, parameter in profile.parameters.items()
                    },
                }
                for model, profile in self.models.items()
            },
        }


def parse_runninghub_catalog(raw):
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "base_url", "default_model", "models"}:
        raise ConfigError("Invalid RunningHub catalog fields.")
    if raw["schema_version"] != 1 or type(raw["schema_version"]) is not int:
        raise ConfigError("Unsupported RunningHub catalog schema.")
    base_url = normalize_base_url(raw["base_url"], "RunningHub base_url")
    models = {}
    if not isinstance(raw["models"], list) or not raw["models"]:
        raise ConfigError("RunningHub models must be a nonempty list.")
    allowed_parameters = {"aspectRatio", "resolution", "quality"}
    for item in raw["models"]:
        if not isinstance(item, dict) or set(item) - {"id", "label", "channel", "endpoint", "parameters", "fixed"}:
            raise ConfigError("Invalid RunningHub model fields.")
        if set(item) < {"id", "label", "channel", "endpoint", "parameters"}:
            raise ConfigError("Missing RunningHub model fields.")
        model = item["id"]
        if not isinstance(model, str) or not model.startswith("rh:") or model in models:
            raise ConfigError("Invalid or duplicate RunningHub model ID.")
        if item["channel"] not in {"economy", "stable"}:
            raise ConfigError("Invalid RunningHub channel.")
        endpoint = item["endpoint"]
        if not isinstance(endpoint, str) or not endpoint.startswith("/openapi/v2/") or ".." in endpoint:
            raise ConfigError("Invalid RunningHub endpoint.")
        parameters = item["parameters"]
        if not isinstance(parameters, dict) or not parameters or set(parameters) - allowed_parameters:
            raise ConfigError("Invalid RunningHub parameters.")
        parsed = {}
        for name, rule in parameters.items():
            if not isinstance(rule, dict) or set(rule) != {"default", "options"}:
                raise ConfigError("Invalid RunningHub parameter rule.")
            options = rule["options"]
            if not isinstance(options, list) or not options or any(not isinstance(v, str) or not v for v in options):
                raise ConfigError("Invalid RunningHub parameter options.")
            if len(options) != len(set(options)) or rule["default"] not in options:
                raise ConfigError("Invalid RunningHub parameter default.")
            parsed[name] = Parameter(tuple((value, value) for value in options), rule["default"])
        fixed = item.get("fixed", {})
        if not isinstance(fixed, dict) or set(fixed) - {"background", "outputFormat"}:
            raise ConfigError("Invalid RunningHub fixed parameters.")
        if fixed and fixed != {"background": "opaque", "outputFormat": "png"}:
            raise ConfigError("Unsupported RunningHub fixed parameters.")
        label = item["label"]
        if not isinstance(label, str) or not label.strip():
            raise ConfigError("Invalid RunningHub model label.")
        models[model] = RunningHubModel(
            label.strip(), item["channel"], endpoint, MappingProxyType(parsed), MappingProxyType(fixed)
        )
    default = raw["default_model"]
    if default not in models:
        raise ConfigError("RunningHub default_model must be enabled.")
    return RunningHubCatalog(base_url, default, MappingProxyType(models))


@lru_cache(maxsize=1)
def get_runninghub_catalog():
    path = Path(__file__).with_name("runninghub_config.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError("Cannot read valid JSON from runninghub_config.json.") from None
    return parse_runninghub_catalog(raw)
