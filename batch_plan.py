"""Pure positional image grouping and Base-major prompt variants."""

import re
from dataclasses import dataclass

from .references import ReferenceSet, normalize_reference


@dataclass(frozen=True)
class TaskSpec:
    task_index: int
    base_index: int
    prompt_index: int


@dataclass(frozen=True)
class BatchPlan:
    columns: tuple[ReferenceSet, ...]
    prompts: tuple[str, ...]
    prompt_source: str
    base_count: int

    @property
    def total(self):
        return self.base_count * len(self.prompts)

    def tasks(self):
        for base in range(1, self.base_count + 1):
            for prompt in range(1, len(self.prompts) + 1):
                yield TaskSpec((base - 1) * len(self.prompts) + prompt, base, prompt)

    def input_index(self, column, base):
        return 0 if len(self.columns[column].images) == 1 else base - 1


def prompt_variants(prompt, prompts):
    if prompts is None:
        prompts = []
    if not isinstance(prompts, list):
        raise ValueError("Prompts must be a STRING list or one wrapped prompt array.")
    if len(prompts) == 1 and isinstance(prompts[0], list):
        prompts = prompts[0]
    if any(not isinstance(p, str) for p in prompts):
        raise ValueError("Every Prompt Variant must be a string.")
    if prompts:
        return tuple(prompts), "prompts"
    if not isinstance(prompt, list) or len(prompt) != 1 or not isinstance(prompt[0], str):
        raise ValueError("Prompt must contain exactly one string when Prompts is empty.")
    return (prompt[0],), "prompt"


def plan_batch(references, prompt, prompts=None, *, local_limit=10, model_limit=None,
               check_cancel=lambda: None):
    if not isinstance(references, dict) or not references:
        raise ValueError("Connect at least Reference 1.")
    names = []
    for name in references:
        match = re.fullmatch(r"reference_([1-9][0-9]*)", name)
        if not match:
            raise ValueError("Invalid Reference socket name.")
        names.append((int(match[1]), name))
    names.sort()
    if [i for i, _ in names] != list(range(1, len(names) + 1)):
        raise ValueError("Reference sockets must be consecutive from Reference 1; a connection is missing.")
    if len(names) > local_limit:
        raise ValueError(f"Reference count exceeds local safety limit ({local_limit}); not a provider limit.")
    if model_limit is not None and len(names) > model_limit:
        raise ValueError(f"Reference count exceeds the configured official model limit ({model_limit}).")
    columns = tuple(normalize_reference(references[name], check_cancel) for _, name in names)
    count = max(len(c.images) for c in columns)
    for index, column in enumerate(columns, 1):
        if len(column.images) not in (1, count):
            raise ValueError(f"reference_{index} contains {len(column.images)} images; expected 1 or {count}.")
    variants, source = prompt_variants(prompt, prompts)
    return BatchPlan(columns, variants, source, count)


def validate_options(concurrency, prefix, api_key):
    if type(concurrency) is not int or not 2 <= concurrency <= 10:
        raise ValueError("Max concurrency must be an integer from 2 to 10.")
    if (
        not isinstance(prefix, str) or not prefix.strip() or prefix != prefix.strip()
        or len(prefix) > 100 or any(ord(c) < 32 for c in prefix)
        or any(c in prefix for c in '/\\<>:"|?*') or prefix in {".", ".."}
        or prefix.endswith(".") or api_key in prefix
    ):
        raise ValueError("Output prefix must be a safe filename fragment without credentials.")
