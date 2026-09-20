"""Compatible image API custom nodes for ComfyUI V3."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    from .config import get_config
    from .diagnostics import initialize_diagnostics
    from .host import install_host
    from .api_config import ImageAPIConfig
    from .nodes import ImageGenerate
    from .batch_nodes import BatchImageGenerate, ImageAPILoadImagesFromFolder

    class ImageAPIExtension(ComfyExtension):
        async def on_load(self):
            get_config()
            initialize_diagnostics()
            install_host()

        async def get_node_list(self):
            return [
                ImageAPIConfig,
                ImageGenerate,
                ImageAPILoadImagesFromFolder,
                BatchImageGenerate,
            ]

    return ImageAPIExtension()
