"""Workflow-owned credentials and endpoint configuration."""

from comfy_api.latest import io

from .api_settings import build_api_config
from .config import get_config

APIConfigType = io.Custom("IMAGE_API_CONFIG")


class APIConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="GRSAIAPIConfig",
            display_name="API Config",
            category="Image API",
            description="Connection settings shared by image generation nodes.",
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
            ],
            outputs=[APIConfigType.Output("config", display_name="Config")],
        )

    @classmethod
    def execute(cls, api_key, base_url, token):
        settings = build_api_config(api_key, base_url, token, get_config().base_url)
        return io.NodeOutput(settings)
