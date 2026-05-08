"""Tests for src/utils/logging module."""

import json
import logging
import tempfile
from pathlib import Path

import structlog

from src.utils.logging import get_logger, setup_logging


class TestSetupLogging:
    """Tests for setup_logging configuration."""

    def setup_method(self) -> None:
        """Reset logging state between tests."""
        root = logging.getLogger()
        root.handlers.clear()
        structlog.reset_defaults()

    def test_default_json_output(self, capsys: object) -> None:
        """JSON renderer is used when json_output=True (default)."""
        setup_logging(level="INFO", json_output=True)
        log = get_logger()
        log.info("test_event", key="value")

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        parsed = json.loads(captured.out.strip())
        assert parsed["event"] == "test_event"
        assert parsed["key"] == "value"
        assert parsed["level"] == "info"
        assert "timestamp" in parsed

    def test_console_output(self) -> None:
        """Console renderer is used when json_output=False."""
        setup_logging(level="INFO", json_output=False)
        log = get_logger()
        # Just verify it doesn't raise — console output isn't JSON
        log.info("console_test", data=42)

    def test_log_level_filtering(self, capsys: object) -> None:
        """Messages below configured level are suppressed."""
        setup_logging(level="WARNING", json_output=True)
        log = get_logger()
        log.info("should_not_appear")
        log.warning("should_appear")

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        lines = [ln for ln in captured.out.strip().splitlines() if ln]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event"] == "should_appear"

    def test_file_rotation_handler(self) -> None:
        """RotatingFileHandler is added when log_file is specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "bot.log")
            setup_logging(level="DEBUG", json_output=True, log_file=log_path)

            log = get_logger()
            log.info("file_test", number=123)

            # Verify file was written
            content = Path(log_path).read_text()
            parsed = json.loads(content.strip())
            assert parsed["event"] == "file_test"
            assert parsed["number"] == 123

    def test_file_handler_rotation_config(self) -> None:
        """File handler uses 10MB max size and 5 backups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "bot.log")
            setup_logging(level="INFO", log_file=log_path)

            root = logging.getLogger()
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 1
            handler = file_handlers[0]
            assert handler.maxBytes == 10 * 1024 * 1024
            assert handler.backupCount == 5

    def test_no_file_handler_when_none(self) -> None:
        """No file handler when log_file is not provided."""
        setup_logging(level="INFO", log_file=None)
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 0

    def test_handlers_cleared_on_reinit(self) -> None:
        """Calling setup_logging twice doesn't duplicate handlers."""
        setup_logging(level="INFO")
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        # Should have exactly 1 console handler
        assert len(root.handlers) == 1


class TestGetLogger:
    """Tests for get_logger convenience function."""

    def setup_method(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        structlog.reset_defaults()

    def test_returns_bound_logger(self) -> None:
        """get_logger returns a structlog BoundLogger instance."""
        setup_logging()
        log = get_logger(component="test")
        assert log is not None

    def test_context_propagation(self, capsys: object) -> None:
        """Bound context appears in log output."""
        setup_logging(level="INFO", json_output=True)
        log = get_logger(component="order_manager")
        log.info("order_placed", order_id=42)

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        parsed = json.loads(captured.out.strip())
        assert parsed["component"] == "order_manager"
        assert parsed["order_id"] == 42
