"""Pure request construction; no authentication or network side effects."""

from .config import Config


def normalize_key(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("API key must not be empty.")
    key = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("API key contains invalid control characters.")
    return key


def build_request(model, prompt, parameters, encoded_images, config: Config):
    profile = config.profile(model)
    if not isinstance(prompt, str):
        raise ValueError("Exactly one string prompt is required.")
    if not isinstance(parameters, dict):
        raise ValueError("Model parameters must be an object.")
    if (
        not isinstance(encoded_images, list)
        or not 1 <= len(encoded_images) <= 10
        or any(not isinstance(i, str) or not i for i in encoded_images)
    ):
        raise ValueError("A request requires 1 to 10 encoded reference images.")
    request = {"model": model, "prompt": prompt, "images": list(encoded_images), "replyType": "async"}
    for name, rule in profile.parameters.items():
        value = parameters.get(name)
        if not isinstance(value, str) or value not in rule.values:
            raise ValueError(
                f"Invalid or missing {name} for the selected model; update the workflow explicitly."
            )
        request[name] = value
    return request


def build_runninghub_request(model, prompt, parameters, image_urls, catalog):
    profile = catalog.profile(model)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Exactly one nonempty string prompt is required.")
    if not isinstance(parameters, dict):
        raise ValueError("Model parameters must be an object.")
    if not isinstance(image_urls, list) or not 1 <= len(image_urls) <= 10:
        raise ValueError("RunningHub requires 1 to 10 reference image URLs.")
    if any(not isinstance(url, str) or not url for url in image_urls):
        raise ValueError("RunningHub image URLs must be nonempty strings.")
    request = {"prompt": prompt, "imageUrls": list(image_urls), **profile.fixed}
    for name, rule in profile.parameters.items():
        value = parameters.get(name)
        if not isinstance(value, str) or value not in rule.values:
            raise ValueError(f"Invalid or missing {name} for the selected model; update the workflow explicitly.")
        if name == "aspectRatio" and value == "auto":
            continue
        request[name] = value
    return request
