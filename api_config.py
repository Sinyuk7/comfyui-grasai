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
            node_id="ImageAPIConfig",
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
                    tooltip="Required for generation. Saved in the workflow and may be included in image metadata.",
                ),
                io.String.Input(
                    "base_url",
                    display_name="Base URL",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Optional API host. Leave empty to use the server default.",
                ),
                io.String.Input(
                    "token",
                    display_name="Token",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Optional account token for balance checks. It is not used for generation.",
                ),
                io.Combo.Input(
                    "provider",
                    display_name="Provider",
                    options=["grsai", "runninghub"],
                    default="grsai",
                    tooltip="Select the API provider used by connected generation nodes.",
                ),
            ],
            outputs=[APIConfigType.Output("config", display_name="Config")],
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
