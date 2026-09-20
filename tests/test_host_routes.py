from dataclasses import replace

import pytest

from grsai.host import model_status_base_url


def test_model_status_allows_official_and_server_configured_hosts(config):
    assert model_status_base_url("https://grsaiapi.com/", config) == "https://grsaiapi.com"
    custom = replace(config, base_url="https://relay.example")
    assert model_status_base_url("https://relay.example", custom) == "https://relay.example"


def test_model_status_rejects_unapproved_runtime_host(config):
    with pytest.raises(ValueError, match="disabled"):
        model_status_base_url("http://127.0.0.1:8080", config)
