"""ComfyUI V3 adapter for one ordered, non-cached generation request."""

import asyncio
import logging

from comfy_api.latest import io

from .client import GrsaiClient
from .config import get_config
from .images import encode_images
from .request_builder import build_request, normalize_key

logger = logging.getLogger(__name__)


def scalar(value, name):
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"{name} must contain exactly one value; scalar broadcasting is not supported.")
    return value[0]


def normalize_inputs(api_key, model, prompt):
    key = normalize_key(scalar(api_key, "api_key"))
    text = scalar(prompt, "prompt")
    # V3 build_nested_inputs preserves the list wrapper on each dictionary value.
    if not isinstance(model, dict) or "model" not in model:
        raise ValueError("Invalid dynamic model input; update the workflow explicitly.")
    selected = scalar(model["model"], "model")
    parameters = {name: scalar(value, name) for name, value in model.items() if name != "model"}
    return key, selected, text, parameters


class GRSAIImageGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        config = get_config()
        options = []
        for model, profile in config.models.items():
            inputs = []
            for name, parameter in profile.parameters.items():
                label = {
                    "aspectRatio": "Aspect ratio" if profile.family == "nano_banana" else "Image size",
                    "imageSize": "Resolution",
                    "quality": "Quality",
                }[name]
                inputs.append(
                    io.Combo.Input(
                        name, options=list(parameter.values), default=parameter.default, display_name=label
                    )
                )
            options.append(io.DynamicCombo.Option(model, inputs))
        return io.Schema(
            node_id="GRSAIImageGenerate",
            display_name="GRSAI Image",
            category="GRSAI",
            inputs=[
                io.String.Input(
                    "api_key",
                    default="",
                    multiline=False,
                    socketless=True,
                    tooltip="Saved in the workflow, including image metadata when enabled. Not secure key storage.",
                ),
                io.DynamicCombo.Input("model", options=options, extra_dict={"default": config.default_model}),
                io.String.Input("prompt", default="", multiline=True, dynamic_prompts=False),
                io.Image.Input("images", optional=True),
            ],
            outputs=[io.Image.Output("images", is_output_list=True)],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    async def execute(cls, api_key=None, model=None, prompt=None, images=None):
        from comfy import model_management
        from .host import execution_ui

        config = get_config()
        key, selected, text, parameters = normalize_inputs(api_key, model, prompt)
        # Validate cheap fields before encoding potentially large images.
        build_request(selected, text, parameters, [], config)

        def check_cancel():
            # Do not consume/reset the global interrupt flag: other async nodes need it too.
            if model_management.processing_interrupted():
                raise model_management.InterruptProcessingException()

        check_cancel()
        encoded = encode_images(images, config.transport.image_encoding, check_cancel)
        request = build_request(selected, text, parameters, encoded, config)
        ui = execution_ui(cls.hidden, config, key)
        client = GrsaiClient(config, key, check_cancel, ui.progress)
        logger.info("GRSAI generation model=%s input_images=%d", selected, len(encoded))
        interrupted = False
        try:
            result = await client.generate(request)
            return io.NodeOutput(result)
        except (model_management.InterruptProcessingException, asyncio.CancelledError):
            interrupted = True
            ui.stale()
            raise
        finally:
            # Scheduling failure must never replace the generation result or its original error.
            if client.submitted and not interrupted and not model_management.processing_interrupted():
                ui.refresh_balance()
