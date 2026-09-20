"""ComfyUI V3 adapter for one ordered, non-cached generation request."""

import asyncio
import logging
import time

from comfy_api.latest import io

from .api_config import APIConfigType
from .api_settings import require_api_config
from .client import GrsaiClient
from .config import get_config
from .diagnostics import log_event, new_run_id
from .errors import clean_message
from .images import encode_images
from .request_builder import build_request

def scalar(value, name):
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"{name} must contain exactly one value; scalar broadcasting is not supported.")
    return value[0]


def normalize_inputs(api_config, model, prompt):
    settings = require_api_config(scalar(api_config, "api_config"))
    text = scalar(prompt, "prompt")
    # V3 build_nested_inputs preserves the list wrapper on each dictionary value.
    if not isinstance(model, dict) or "model" not in model:
        raise ValueError("Invalid dynamic model input; update the workflow explicitly.")
    selected = scalar(model["model"], "model")
    parameters = {name: scalar(value, name) for name, value in model.items() if name != "model"}
    return settings, selected, text, parameters


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
                        name,
                        options=list(parameter.values),
                        default=parameter.default,
                        display_name=label,
                        tooltip=f"{label} supported by the selected model.",
                    )
                )
            options.append(io.DynamicCombo.Option(model, inputs))
        return io.Schema(
            node_id="GRSAIImageGenerate",
            display_name="Image Generate",
            category="Image API",
            description="Generate images with a compatible asynchronous image API.",
            inputs=[
                APIConfigType.Input(
                    "api_config",
                    display_name="Config",
                    tooltip="Connect an API Config node.",
                ),
                io.DynamicCombo.Input(
                    "model",
                    display_name="Model",
                    options=options,
                    extra_dict={"default": config.default_model},
                    tooltip="Select a model. Availability checks are advisory only.",
                ),
                io.String.Input(
                    "prompt",
                    display_name="Prompt",
                    default="",
                    multiline=True,
                    dynamic_prompts=False,
                    tooltip="Instructions for the generated image.",
                ),
                io.Image.Input(
                    "images",
                    display_name="Images",
                    optional=True,
                    tooltip="Optional reference images used together in one request.",
                ),
            ],
            outputs=[io.Image.Output("images", display_name="Images", is_output_list=True)],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    async def execute(cls, api_config=None, model=None, prompt=None, images=None):
        from comfy import model_management
        from .host import execution_ui

        settings, selected, text, parameters = normalize_inputs(api_config, model, prompt)
        config = settings.apply(get_config())
        run_id = new_run_id()
        started = time.monotonic()
        node_id = str(cls.hidden.unique_id)
        log_event("generation.started", run_id=run_id, node_id=node_id, model=selected)
        # Validate cheap fields before encoding potentially large images.
        client = None
        ui = None
        interrupted = False

        def check_cancel():
            # Do not consume/reset the global interrupt flag: other async nodes need it too.
            if model_management.processing_interrupted():
                raise model_management.InterruptProcessingException()

        try:
            build_request(selected, text, parameters, [], config)
            check_cancel()
            encoded = encode_images(images, config.transport.image_encoding, check_cancel)
            request = build_request(selected, text, parameters, encoded, config)
            ui = execution_ui(cls.hidden, config, settings.token)
            client = GrsaiClient(
                config,
                settings.api_key,
                check_cancel,
                ui.progress,
                log_context={"run_id": run_id, "node_id": node_id},
            )
            result = await client.generate(request)
            log_event(
                "generation.succeeded",
                run_id=run_id,
                node_id=node_id,
                task_id=client.task_id,
                outputs=len(result),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            return io.NodeOutput(result)
        except (model_management.InterruptProcessingException, asyncio.CancelledError):
            interrupted = True
            if ui:
                ui.stale()
            log_event(
                "generation.interrupted",
                level=logging.WARNING,
                run_id=run_id,
                node_id=node_id,
                task_id=client.task_id if client else None,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        except Exception as exc:
            log_event(
                "generation.failed",
                level=logging.ERROR,
                run_id=run_id,
                node_id=node_id,
                task_id=client.task_id if client else None,
                error_type=type(exc).__name__,
                error=clean_message(str(exc), (settings.api_key, text)),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        finally:
            # Scheduling failure must never replace the generation result or its original error.
            if client and client.submitted and not interrupted and not model_management.processing_interrupted():
                ui.refresh_balance()
