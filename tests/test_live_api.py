import argparse
import json
from pathlib import Path

import pytest
from PIL import Image

import live_api


def test_fixture_roles_and_1k_parameters():
    case = json.loads((Path(__file__).parent / "live_case.json").read_text())
    assert case["images"] == ["图片1 人物.webp", "图片 2 上衣.webp", "图片 3 裤子.webp"]
    assert case["models"]["nano-banana-2"] == {"aspectRatio": "3:4", "imageSize": "1K"}
    assert case["models"]["gpt-image-2.5"] == {"aspectRatio": "1090x1443", "quality": "auto"}


def test_report_refuses_secret(tmp_path):
    with pytest.raises(RuntimeError, match="credentials"):
        live_api.write_report(tmp_path / "report.json", {"error": "test-secret"}, "test-secret")
    assert not (tmp_path / "report.json").exists()


async def test_prepare_only_never_calls_network(tmp_path, monkeypatch):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    names = ["base.png", "top.png", "pants.png"]
    for name in names:
        Image.new("RGB", (3, 4)).save(image_dir / name)
    fixture = json.loads((Path(__file__).parent / "live_case.json").read_text())
    fixture["images"] = names
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(fixture))

    async def forbidden(*args, **kwargs):
        raise AssertionError("Offline preparation must never call generate")

    monkeypatch.setattr(live_api.ObservedClient, "generate", forbidden)
    args = argparse.Namespace(
        case=case_path, images=image_dir, models=None, output=tmp_path / "output", prepare_only=True
    )
    assert await live_api.run(args) == 0
    report = json.loads((args.output / "report.json").read_text())
    assert [run["status"] for run in report["runs"]] == ["prepared", "prepared"]
    assert all(not run["requests"] for run in report["runs"])
