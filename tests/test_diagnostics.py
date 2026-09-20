import logging

from grsai import diagnostics


def test_rotating_diagnostic_log_is_bounded_and_singleton(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 180)
    logger = logging.getLogger(diagnostics.LOG_NAME)
    previous_handlers = list(logger.handlers)
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.handlers.clear()
    try:
        path = diagnostics.initialize_diagnostics(tmp_path)
        assert diagnostics.initialize_diagnostics(tmp_path) == path
        assert len(logger.handlers) == 1

        for index in range(40):
            diagnostics.log_event("test.event", run_id="run-1", index=index, detail="x" * 40)
        logger.handlers[0].flush()

        files = sorted(tmp_path.glob("image_api.log*"))
        assert path == tmp_path / "image_api.log"
        assert len(files) == diagnostics.BACKUP_COUNT + 1
        assert all(file.stat().st_size <= diagnostics.MAX_BYTES for file in files)
        assert "test.event" in path.read_text()
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_event_fields_remain_one_line(caplog):
    with caplog.at_level(logging.INFO, logger=diagnostics.LOG_NAME):
        diagnostics.log_event("generation.failed", run_id="abc", error="line one\nline two")
    assert 'error="line one\\nline two"' in caplog.text
