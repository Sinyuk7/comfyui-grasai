import base64
from io import BytesIO

import pytest
import torch
from PIL import Image

from grsai.config import ConfigError, load_config, parse_config
from grsai.images import decode_image, encode_images
from grsai.request_builder import build_request, normalize_key


def test_catalog_and_immutable_snapshot(config):
    assert list(config.models) == [
        "nano-banana-2",
        "nano-banana-pro",
        "gpt-image-2",
        "gpt-image-2-vip",
        "gpt-image-2.5",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst",
    ]
    assert config.transport.task_timeout_seconds is None
    with pytest.raises(TypeError):
        config.models["other"] = None
    with pytest.raises(TypeError):
        config.models[config.default_model].parameters["quality"] = None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(schema_version=True),
        lambda c: c.update(schema_version=2),
        lambda c: c.update(base_url="https://example.com/v1"),
        lambda c: c.update(base_url="https://key@example.com"),
        lambda c: c.update(default_model="removed"),
        lambda c: c["model_groups"].append(c["model_groups"][0]),
        lambda c: c["model_groups"][0].update(profile="missing"),
        lambda c: c["model_groups"][0].update(enabled="true"),
        lambda c: c["parameter_presets"]["nano_ratios"].append({"value": "auto", "label": "duplicate"}),
        lambda c: c["profiles"]["nano2"]["parameters"]["aspectRatio"].update(default="invalid"),
        lambda c: c["profiles"]["nano2"]["parameters"].update(
            prompt={"preset": "nano_ratios", "default": "auto"}
        ),
        lambda c: c["transport"].update(task_timeout_seconds=0),
        lambda c: c["transport"].update(poll_interval_seconds=float("inf")),
        lambda c: c["transport"].update(download_retry_limit=4),
        lambda c: c["transport"].update(generate_path="/anything"),
    ],
)
def test_invalid_config(raw_config, mutation):
    mutation(raw_config)
    with pytest.raises(ConfigError):
        parse_config(raw_config)


def test_config_override(tmp_path, raw_config):
    import json

    (tmp_path / "grsai_config.example.json").write_text(json.dumps(raw_config))
    assert load_config(tmp_path).default_model == "nano-banana-2"
    raw_config["default_model"] = "gpt-image-2"
    (tmp_path / "grsai_config.json").write_text(json.dumps(raw_config))
    assert load_config(tmp_path).default_model == "gpt-image-2"
    (tmp_path / "grsai_config.json").write_text("invalid")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_family_allowlist_and_invalid_workflow(config):
    for model, profile in config.models.items():
        params = {name: param.default for name, param in profile.parameters.items()}
        body = build_request(
            model,
            "text",
            {**params, "prompt": "ignored", "seed": 10, "background": "transparent"},
            ["encoded"],
            config,
        )
        assert set(body) == {"model", "prompt", "images", "replyType", *profile.parameters}
        assert body["prompt"] == "text"
        assert body["replyType"] == "async"
        with pytest.raises(ValueError):
            build_request(model, "text", {**params, "aspectRatio": "removed-option"}, ["encoded"], config)
    with pytest.raises(ConfigError):
        build_request("deleted", "text", {}, ["encoded"], config)
    assert normalize_key("  not-a-prefixed-key  ") == "not-a-prefixed-key"
    for key in [" ", None, "a\nb"]:
        with pytest.raises(ValueError):
            normalize_key(key)


@pytest.mark.parametrize("encoding", ["base64_png", "data_url_png"])
def test_ordered_batches_and_dimensions(encoding):
    batches = [torch.stack([torch.zeros(3, 4, 3), torch.ones(3, 4, 3)]), torch.full((1, 5, 2, 3), 0.5)]
    values = encode_images(batches, encoding)
    decoded = [decode_image(base64.b64decode(value.split(",")[-1])) for value in values]
    assert [tuple(value.shape) for value in decoded] == [(1, 3, 4, 3), (1, 3, 4, 3), (1, 5, 2, 3)]
    assert decoded[0].mean() == 0 and decoded[1].mean() == 1
    assert abs(decoded[2].mean().item() - 0.5) < 0.005
    assert encode_images(None) == []
    assert encode_images([]) == []


@pytest.mark.parametrize(
    "bad",
    [
        torch.zeros(1, 3, 4, 4),
        torch.zeros(3, 4, 3),
        torch.full((1, 3, 4, 3), float("nan")),
        torch.ones(1, 3, 4, 3) * 2,
        torch.zeros(0, 3, 4, 3),
    ],
)
def test_invalid_image_is_not_skipped(bad):
    with pytest.raises(ValueError, match="(image 2|batch 2)"):
        encode_images([torch.zeros(1, 3, 4, 3), bad])


def png(size=(4, 3), color=(255, 0, 0), mode="RGB"):
    stream = BytesIO()
    Image.new(mode, size, color).save(stream, "PNG")
    return stream.getvalue()


def test_alpha_and_decode():
    with pytest.raises(ValueError, match="alpha"):
        decode_image(png(color=(10, 20, 30, 0), mode="RGBA"))
    assert decode_image(png(color=(10, 20, 30, 255), mode="RGBA")).shape == (1, 3, 4, 3)
    with pytest.raises(ValueError, match="decodable"):
        decode_image(b"not an image")
