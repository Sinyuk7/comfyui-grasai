"""Workflow-owned credentials and endpoint configuration."""

from comfy_api.latest import io

from .api_settings import build_provider_config
from .config import get_config
from .runninghub_config import get_runninghub_catalog

APIConfigType = io.Custom("IMAGE_API_CONFIG")


class ImageAPIConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SinyukImageAPIConfig",
            display_name="API Config",
            category="Image API",
            description="Connection settings shared by image generation nodes.",
            search_aliases=["GRSAI API Config", "RunningHub API Config"],
            inputs=[
                io.String.Input(
                    "api_key",
                    display_name="API Key",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Provider API key used for generation. Saved in the workflow and may be included in image metadata.",
                ),
                io.String.Input(
                    "base_url",
                    display_name="Base URL",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Optional API host for the selected Provider. Leave empty to use that Provider's default.",
                ),
                io.String.Input(
                    "token",
                    display_name="Token",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Optional GRSAI account token used only for balance checks. RunningHub ignores this value.",
                ),
                io.Combo.Input(
                    "provider",
                    display_name="Provider",
                    options=["grsai", "runninghub"],
                    default="grsai",
                    tooltip="Select the API provider used by connected generation nodes. Each Provider keeps its own Base URL.",
                ),
            ],
            outputs=[APIConfigType.Output(
                "config",
                display_name="Config",
                tooltip="Validated Provider, credentials, and endpoint settings for Image API generation nodes.",
            )],
        )

    @classmethod
    def execute(cls, api_key, base_url, token, provider="grsai"):
        settings = build_provider_config(
            api_key,
            base_url,
            token,
            provider,
            get_config().base_url,
            get_runninghub_catalog().base_url,
        )
        return io.NodeOutput(settings)
