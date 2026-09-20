"""ComfyUI V3 adapters for folder snapshots and a single paid batch execution."""

import asyncio

from comfy_api.latest import io

from .api_settings import require_api_config
from .batch_plan import plan_batch, validate_options
from .batch_runner import BatchRunner
from .config import get_config
from .diagnostics import new_run_id
from .nodes import ImageGenerate, provider_profile, scalar
from .references import load_folder

ReferenceType = io.Custom("IMAGE_API_REFERENCES")


def check_cancel():
    from comfy import model_management

    if model_management.processing_interrupted():
        raise model_management.InterruptProcessingException()


class ImageAPILoadImagesFromFolder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SinyukImageAPILoadFolder", display_name="Load Images From Folder",
            category="Image API", is_input_list=True,
            description="Load an ordered, non-recursive image set from a folder on the ComfyUI server.",
            search_aliases=["GRSAI Load Images From Folder"],
            inputs=[io.String.Input("folder", default="", display_name="Folder",
                                    tooltip="Folder on the ComfyUI server. Loads supported images non-recursively in natural filename order; re-scans on every run and does not resize.")],
            outputs=[ReferenceType.Output(
                        "references", display_name="References",
                        tooltip="Ordered images plus source filenames for a Batch Image Generate Reference input."),
                     io.Image.Output(
                        "images", display_name="Images", is_output_list=True,
                        tooltip="The same ordered images as a standard IMAGE list for Preview, Save, or processing nodes.")],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    def execute(cls, folder):
        references = load_folder(scalar(folder, "folder"), check_cancel)
        return io.NodeOutput(references, list(references.images),
                             ui={"image_api_files": [source.filename for source in references.sources]})


class BatchImageGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        config = get_config()
        shared = ImageGenerate.define_schema().inputs[:3]
        for input_, label in zip(shared, ("Config", "Model", "Prompt")):
            input_.display_name = label
        template = io.Autogrow.TemplateNames(
            io.MultiType.Input(
                "reference",
                types=[ReferenceType, io.Image],
                tooltip="One ordered reference source. Connect a folder References output or a standard IMAGE input.",
            ),
            names=[f"reference_{i}" for i in range(1, config.batch_reference_limit + 1)], min=1,
        )
        return io.Schema(
            node_id="SinyukImageAPIBatchGenerate", display_name="Batch Image Generate", category="Image API",
            description="Generate and save an ordered set of image and prompt combinations.",
            search_aliases=["GRSAI Batch Image Generate", "RunningHub Batch Image Generate"],
            inputs=[io.Autogrow.Input("references", display_name="References", template=template,
                                     tooltip="Connect 1-10 ordered reference sources. Each task receives one image from every connected source."),
                    io.String.Input("prompts", display_name="Prompts", optional=True, force_input=True,
                                    tooltip="Optional STRING list. Nonempty values override Prompt and create every base x prompt combination, not index pairing."),
                    *shared,
                    io.Int.Input("max_concurrency", display_name="Max Concurrency", default=4, min=2, max=10,
                                 tooltip="Maximum paid generation tasks running at once (2-10). Higher values may hit Provider rate limits."),
                    io.String.Input("output_prefix", display_name="Output Prefix", default="ImageAPI",
                                    tooltip="Filename prefix inside a new batch folder under ComfyUI output. A manifest is saved beside the images.")],
            outputs=[io.Image.Output(
                        "images", display_name="Images", is_output_list=True,
                        tooltip="Successful generated images flattened in task and result order; failed tasks are omitted."),
                     io.String.Output(
                        "manifest", display_name="Manifest",
                        tooltip="Absolute path to the JSON manifest containing task mapping, output files, remote IDs, and errors.")],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_input_list=True, is_output_node=True, not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    async def execute(cls, references, api_config, model, prompt, max_concurrency, output_prefix, prompts=None):
        import folder_paths
        from comfy import model_management
        from .host import execution_ui

        settings = require_api_config(scalar(api_config, "api_config"))
        config = settings.apply(get_config())
        key = settings.api_key
        if not isinstance(model, dict) or "model" not in model:
            raise ValueError("Invalid dynamic model input; update the workflow explicitly.")
        selected = scalar(model["model"], "model")
        parameters = {name: scalar(value, name) for name, value in model.items() if name != "model"}
        concurrency, prefix = scalar(max_concurrency, "max_concurrency"), scalar(output_prefix, "output_prefix")
        validate_options(concurrency, prefix, key)
        profile = provider_profile(settings, selected)
        plan = plan_batch(references, prompt, prompts, local_limit=min(config.batch_reference_limit, 10),
                          model_limit=profile.max_reference_images, check_cancel=check_cancel)
        check_cancel()
        ui = execution_ui(cls.hidden, config, settings.token, settings.provider)
        runner = BatchRunner(config, key, plan, selected, parameters, concurrency, prefix,
                             folder_paths.get_output_directory(), check_cancel, ui.batch_progress,
                             run_id=new_run_id(), node_id=str(cls.hidden.unique_id),
                             provider=settings.provider)
        interrupted = False
        try:
            images, manifest = await runner.run()
            if not images:
                from comfy_execution.graph_utils import ExecutionBlocker

                # The host maps empty IMAGE lists with index -1. Block only the image branch.
                images = ExecutionBlocker(None)
            return io.NodeOutput(images, manifest, ui={"image_api_manifest": [manifest]})
        except (model_management.InterruptProcessingException, asyncio.CancelledError):
            interrupted = True
            ui.stale()
            raise
        finally:
            if runner.submitted and not interrupted and not model_management.processing_interrupted():
                ui.refresh_balance()
