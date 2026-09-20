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
    if not isinstance(encoded_images, list) or any(not isinstance(i, str) or not i for i in encoded_images):
        raise ValueError("Encoded images must be a list of nonempty strings.")
    request = {"model": model, "prompt": prompt, "images": list(encoded_images), "replyType": "async"}
    for name, rule in profile.parameters.items():
        value = parameters.get(name)
        if not isinstance(value, str) or value not in rule.values:
            raise ValueError(
                f"Invalid or missing {name} for the selected model; update the workflow explicitly."
            )
        request[name] = value
    return request
