"""Compatible image API custom nodes for ComfyUI V3."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    from .config import get_config
    from .diagnostics import initialize_diagnostics
    from .host import install_host
    from .api_config import APIConfig
    from .nodes import GRSAIImageGenerate
    from .batch_nodes import GRSAIBatchImageGenerate, GRSAILoadImagesFromFolder

    class GRSAIExtension(ComfyExtension):
        async def on_load(self):
            get_config()
            initialize_diagnostics()
            install_host()

        async def get_node_list(self):
            return [APIConfig, GRSAIImageGenerate, GRSAILoadImagesFromFolder, GRSAIBatchImageGenerate]

    return GRSAIExtension()
