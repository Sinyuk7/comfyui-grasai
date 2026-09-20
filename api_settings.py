"""Validated runtime credentials and endpoint settings."""

from dataclasses import dataclass, replace

from .config import Config, normalize_base_url
from .request_builder import normalize_key


def _optional_secret(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    secret = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in secret):
        raise ValueError(f"{name} contains invalid control characters.")
    return secret or None


@dataclass(frozen=True)
class RuntimeAPIConfig:
    api_key: str
    base_url: str
    token: str | None = None
    provider: str = "grsai"

    def apply(self, config: Config) -> Config:
        return replace(config, base_url=self.base_url)


def build_provider_config(api_key, base_url, token, provider, grsai_base_url, runninghub_base_url):
    key = normalize_key(api_key)
    if provider not in {"grsai", "runninghub"}:
        raise ValueError("Provider must be GRSAI or RunningHub.")
    if not isinstance(base_url, str):
        raise ValueError("Base URL must be a string.")
    default = grsai_base_url if provider == "grsai" else runninghub_base_url
    if not default:
        raise ValueError("Provider default Base URL is unavailable.")
    endpoint = normalize_base_url(base_url.strip() or default, "Base URL")
    return RuntimeAPIConfig(
        key,
        endpoint,
        _optional_secret(token, "Token") if provider == "grsai" else None,
        provider,
    )


def require_api_config(value):
    if not isinstance(value, RuntimeAPIConfig):
        raise ValueError("Connect an API Config node.")
    return value
