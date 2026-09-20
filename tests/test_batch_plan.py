from dataclasses import replace

import pytest
import torch

from grsai.batch_plan import plan_batch, prompt_variants, validate_options
from grsai.config import ConfigError, parse_config
from grsai.references import ReferenceSet, load_folder, natural_key, normalize_reference
from test_config_images import png


def column(count):
    return [torch.zeros(count, 3, 4, 3)]


@pytest.mark.parametrize("sizes,n", [([1, 10, 10], 10), ([10, 1, 10], 10), ([1, 1], 1), ([10], 10)])
def test_groups_and_base_major(sizes, n):
    plan = plan_batch({f"reference_{i}": column(size) for i, size in enumerate(sizes, 1)},
                      ["ignored"], ["standing", "sitting", "running", "lying"])
    assert plan.base_count == n and plan.total == n * 4
    tasks = list(plan.tasks())
    assert tasks[0].base_index == tasks[3].base_index == 1
    assert [t.prompt_index for t in tasks[:4]] == [1, 2, 3, 4]
    if n >= 4:
        assert (tasks[13].task_index, tasks[13].base_index, tasks[13].prompt_index) == (14, 4, 2)
    for i, size in enumerate(sizes):
        assert plan.input_index(i, n) == (0 if size == 1 else n - 1)


def test_equal_lengths_never_imply_prompt_pairing():
    plan = plan_batch({"reference_1": column(10)}, ["ignored"], [str(i) for i in range(10)])
    assert plan.total == 100


@pytest.mark.parametrize("prompt,prompts,expected,source", [
    (["line1\nline2"], None, ("line1\nline2",), "prompt"),
    (["fallback"], [], ("fallback",), "prompt"),
    (["fallback"], [[]], ("fallback",), "prompt"),
    (None, ["", "same", "same"], ("", "same", "same"), "prompts"),
    (["ignored"], [["first", "second"]], ("first", "second"), "prompts"),
    (["ignored"], ['["a", "b"]'], ('["a", "b"]',), "prompts"),
])
def test_prompt_precedence(prompt, prompts, expected, source):
    assert prompt_variants(prompt, prompts) == (expected, source)


@pytest.mark.parametrize("prompt,prompts", [(["x"], [1]), (["x"], [[["x"]]]), (["x"], "text"),
                                             (["x", "y"], []), ([], None)])
def test_invalid_prompts(prompt, prompts):
    with pytest.raises(ValueError):
        prompt_variants(prompt, prompts)


@pytest.mark.parametrize("references,match", [
    ({}, "at least"), ({"reference_1": []}, "nonempty"),
    ({"reference_1": column(10), "reference_2": column(9)}, "expected 1 or 10"),
    ({"reference_1": column(10), "reference_2": column(5)}, "expected 1 or 10"),
    ({"reference_1": column(1), "reference_3": column(1)}, "consecutive"),
    ({"reference_0": column(1)}, "socket name"),
])
def test_invalid_references(references, match):
    with pytest.raises(ValueError, match=match):
        plan_batch(references, ["x"])


def test_numeric_socket_order_and_limits():
    refs = {f"reference_{i}": column(1) for i in range(12, 0, -1)}
    plan = plan_batch(refs, ["x"], local_limit=12)
    assert plan.columns[9].images[0].data_ptr() == refs["reference_10"][0].data_ptr()
    with pytest.raises(ValueError, match="local safety"):
        plan_batch(refs, ["x"])
    with pytest.raises(ValueError, match="official model"):
        plan_batch(refs, ["x"], local_limit=12, model_limit=8)


def test_list_then_batch_order_and_storage_sharing():
    first, second = torch.zeros(2, 3, 4, 3), torch.ones(1, 5, 2, 3)
    refs = normalize_reference([first, second])
    assert [tuple(i.shape) for i in refs.images] == [(1, 3, 4, 3), (1, 3, 4, 3), (1, 5, 2, 3)]
    assert refs.images[0].data_ptr() == first.data_ptr()
    assert refs.images[1].storage_offset() == first[1].storage_offset()
    assert all(s.filename == "unknown" for s in refs.sources)
    assert [s.sequence_index for s in refs.sources] == [1, 2, 3]


def test_folder_sort_snapshot_and_changed_content(tmp_path):
    for name, size in [("10.png", (2, 5)), ("2.png", (4, 3)), ("1.png", (7, 2))]:
        (tmp_path / name).write_bytes(png(size))
    (tmp_path / ".hidden.png").write_bytes(b"invalid")
    (tmp_path / "ignored.txt").write_text("not image")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "0.png").write_bytes(png())
    refs = load_folder(str(tmp_path))
    assert [s.filename for s in refs.sources] == ["1.png", "2.png", "10.png"]
    assert [tuple(i.shape) for i in refs.images] == [(1, 2, 7, 3), (1, 3, 4, 3), (1, 5, 2, 3)]
    normalized = normalize_reference([refs])
    assert normalized.images[0].data_ptr() == refs.images[0].data_ptr()
    (tmp_path / "1.png").write_bytes(png((1, 1)))
    refreshed = load_folder(str(tmp_path))
    assert refreshed.sources[0].sha256 != refs.sources[0].sha256
    assert refs.images[0].shape == (1, 2, 7, 3)


def test_invalid_folder_and_metadata(tmp_path):
    with pytest.raises(ValueError, match="no supported"):
        load_folder(str(tmp_path))
    (tmp_path / "bad.png").write_bytes(b"invalid")
    with pytest.raises(ValueError, match="no images were skipped"):
        load_folder(str(tmp_path))
    refs = normalize_reference(column(1))
    with pytest.raises(ValueError, match="align"):
        normalize_reference([ReferenceSet(refs.images, ())])
    with pytest.raises(ValueError, match="metadata"):
        normalize_reference([replace(refs, sources=(replace(refs.sources[0], sequence_index=0),))])


def test_natural_sort_stable():
    names = ["衣服10.png", "衣服2.png", "衣服1.png", "002.png", "010.png", "001.png", "a2.png", "A2.png"]
    assert sorted(names, key=natural_key) == sorted(reversed(names), key=natural_key)
    assert sorted(names[:3], key=natural_key) == ["衣服1.png", "衣服2.png", "衣服10.png"]
    assert sorted(names[3:6], key=natural_key) == ["001.png", "002.png", "010.png"]


@pytest.mark.parametrize("concurrency,prefix", [(1, "Clothes"), (11, "Clothes"), (True, "Clothes"),
                                              (4, "../x"), (4, "x/y"), (4, ""), (4, "secret")])
def test_options_rejected(concurrency, prefix):
    with pytest.raises(ValueError):
        validate_options(concurrency, prefix, "secret")


def test_optional_config_limits(raw_config):
    raw_config["batch_reference_limit"] = 12
    with pytest.raises(ConfigError):
        parse_config(raw_config)
    raw_config["batch_reference_limit"] = 10
    profile = next(iter(raw_config["profiles"].values()))
    profile.update(max_reference_images=8, reference_limit_source="Documented operator-provided evidence")
    cfg = parse_config(raw_config)
    assert cfg.batch_reference_limit == 10
    assert any(p.max_reference_images == 8 for p in cfg.models.values())
    del profile["reference_limit_source"]
    with pytest.raises(ConfigError):
        parse_config(raw_config)
