"""Keep untrusted upstream diagnostics bounded and credential-free."""

import re


def clean_message(value, secrets=()):
    text = value if isinstance(value, str) else "Upstream returned an invalid error message."
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"data:image/[^\s]+|[A-Za-z0-9+/=]{160,}", "[omitted]", text)
    text = re.sub(r"https?://\S+", "[URL omitted]", text)
    return " ".join(text.split())[:400]


class GrsaiError(RuntimeError):
    def __init__(self, message, task_id=None, index=None):
        self.task_id = task_id
        self.index = index
        suffix = f" [task_id={task_id}]" if task_id else ""
        if index is not None:
            suffix += f" [result={index}]"
        super().__init__(message + suffix)
