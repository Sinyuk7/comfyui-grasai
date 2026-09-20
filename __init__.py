"""GRSAI single-image, folder and batch custom-node extension (ComfyUI V3)."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    from .config import get_config
    from .host import install_host
    from .nodes import GRSAIImageGenerate
    from .batch_nodes import GRSAIBatchImageGenerate, GRSAILoadImagesFromFolder

    class GRSAIExtension(ComfyExtension):
        async def on_load(self):
            get_config()
            install_host()

        async def get_node_list(self):
            return [GRSAIImageGenerate, GRSAILoadImagesFromFolder, GRSAIBatchImageGenerate]

    return GRSAIExtension()
