from dataclasses import replace

import pytest

from grsai.api_settings import RuntimeAPIConfig, build_provider_config, require_api_config
from grsai.config import ConfigError


def test_api_config_uses_default_and_normalizes_secrets(config):
    settings = build_provider_config("  key  ", "", "  token  ", "grsai", config.base_url, None)
    assert settings == RuntimeAPIConfig("key", config.base_url, "token")
    assert settings.apply(config) == config


def test_api_config_applies_runtime_base_url(config):
    settings = build_provider_config(
        "key", "https://relay.example/", "", "grsai", config.base_url, None
    )
    assert settings.base_url == "https://relay.example"
    assert settings.token is None
    assert settings.apply(config) == replace(config, base_url="https://relay.example")


@pytest.mark.parametrize("base_url", ["ftp://example.com", "https://user@example.com", "https://example.com/path"])
def test_api_config_rejects_invalid_base_url(config, base_url):
    with pytest.raises(ConfigError):
        build_provider_config("key", base_url, "", "grsai", config.base_url, None)


def test_api_config_requires_typed_connection():
    with pytest.raises(ValueError, match="Connect an API Config"):
        require_api_config({"api_key": "key"})
