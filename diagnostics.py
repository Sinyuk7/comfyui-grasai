"""Small bounded diagnostic log for generation lifecycle events."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

LOG_NAME = "grsai.diagnostics"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 4
_HANDLER_MARKER = "_grsai_diagnostic_handler"


def _default_directory():
    import folder_paths

    system_directory = getattr(folder_paths, "get_system_user_directory", None)
    if system_directory:
        return Path(system_directory("grsai")) / "logs"
    return Path(folder_paths.get_user_directory()) / "__grsai" / "logs"


def initialize_diagnostics(directory=None):
    """Install one private rotating handler without changing ComfyUI's root logger."""
    logger = logging.getLogger(LOG_NAME)
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return Path(handler.baseFilename)

    path = Path(directory) if directory is not None else _default_directory()
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = path / "grsai.log"
        handler = RotatingFileHandler(
            destination,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        setattr(handler, _HANDLER_MARKER, True)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = True
        logger.info("GRSAI diagnostic log: %s", destination)
        return destination
    except OSError as exc:
        logging.getLogger(__name__).warning("GRSAI diagnostic log unavailable: %s", exc)
        return None


def new_run_id():
    return uuid4().hex[:12]


def log_event(name, *, level=logging.INFO, **fields):
    parts = [name]
    for key, value in fields.items():
        if value is not None:
            parts.append(f"{key}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}")
    logging.getLogger(LOG_NAME).log(level, " ".join(parts))
