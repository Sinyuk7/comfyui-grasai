"""ComfyUI V3 adapters for folder snapshots and a single paid batch execution."""

import asyncio

from comfy_api.latest import io

from .batch_plan import plan_batch, validate_options
from .batch_runner import BatchRunner
from .config import get_config
from .nodes import GRSAIImageGenerate, scalar
from .references import load_folder
from .request_builder import build_request, normalize_key

ReferenceType = io.Custom("GRSAI_REFERENCES")


def check_cancel():
    from comfy import model_management

    if model_management.processing_interrupted():
        raise model_management.InterruptProcessingException()


class GRSAILoadImagesFromFolder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="GRSAILoadImagesFromFolder", display_name="GRSAI Load Images From Folder",
            category="GRSAI", is_input_list=True,
            inputs=[io.String.Input("folder", default="", display_name="Folder",
                                    tooltip="Directory on the ComfyUI server. Natural filename order; no resizing.")],
            outputs=[ReferenceType.Output("references"), io.Image.Output("images", is_output_list=True)],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    def execute(cls, folder):
        references = load_folder(scalar(folder, "folder"), check_cancel)
        return io.NodeOutput(references, list(references.images),
                             ui={"grsai_files": [source.filename for source in references.sources]})


class GRSAIBatchImageGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        config = get_config()
        shared = GRSAIImageGenerate.define_schema().inputs[:3]
        for input_, label in zip(shared, ("API Key", "Model", "Prompt")):
            input_.display_name = label
        template = io.Autogrow.TemplateNames(
            io.MultiType.Input("reference", types=[ReferenceType, io.Image]),
            names=[f"reference_{i}" for i in range(1, config.batch_reference_limit + 1)], min=1,
        )
        return io.Schema(
            node_id="GRSAIBatchImageGenerate", display_name="GRSAI Batch Image", category="GRSAI",
            inputs=[io.Autogrow.Input("references", template=template),
                    io.String.Input("prompts", display_name="Prompts", optional=True, force_input=True,
                                    tooltip="Nonempty STRING list overrides Prompt. Always N x M variants, not pairing."),
                    *shared,
                    io.Int.Input("max_concurrency", display_name="Max concurrency", default=4, min=2, max=10),
                    io.String.Input("output_prefix", display_name="Output prefix", default="GRSAI")],
            outputs=[io.Image.Output("images", is_output_list=True), io.String.Output("manifest")],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_input_list=True, is_output_node=True, not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    async def execute(cls, references, api_key, model, prompt, max_concurrency, output_prefix, prompts=None):
        import folder_paths
        from comfy import model_management
        from .host import execution_ui

        config = get_config()
        key = normalize_key(scalar(api_key, "api_key"))
        if not isinstance(model, dict) or "model" not in model:
            raise ValueError("Invalid dynamic model input; update the workflow explicitly.")
        selected = scalar(model["model"], "model")
        parameters = {name: scalar(value, name) for name, value in model.items() if name != "model"}
        concurrency, prefix = scalar(max_concurrency, "max_concurrency"), scalar(output_prefix, "output_prefix")
        validate_options(concurrency, prefix, key)
        profile = config.profile(selected)
        plan = plan_batch(references, prompt, prompts, local_limit=config.batch_reference_limit,
                          model_limit=profile.max_reference_images, check_cancel=check_cancel)
        for variant in plan.prompts:
            build_request(selected, variant, parameters, [], config)
        check_cancel()
        ui = execution_ui(cls.hidden, config, key)
        runner = BatchRunner(config, key, plan, selected, parameters, concurrency, prefix,
                             folder_paths.get_output_directory(), check_cancel, ui.batch_progress)
        interrupted = False
        try:
            images, manifest = await runner.run()
            if not images:
                from comfy_execution.graph_utils import ExecutionBlocker

                # The host maps empty IMAGE lists with index -1. Block only the image branch.
                images = ExecutionBlocker(None)
            return io.NodeOutput(images, manifest, ui={"grsai_manifest": [manifest]})
        except (model_management.InterruptProcessingException, asyncio.CancelledError):
            interrupted = True
            ui.stale()
            raise
        finally:
            if runner.submitted and not interrupted and not model_management.processing_interrupted():
                ui.refresh_balance()
