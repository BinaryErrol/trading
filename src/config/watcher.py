"""Hot-reload watcher for config file changes.

Uses watchfiles to monitor the YAML config file and reload strategy parameters
on change. Invalid configs are logged but don't interrupt the running bot.
"""

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import structlog
from pydantic import ValidationError
from watchfiles import awatch

from src.config.settings import Settings, load_settings

logger = structlog.get_logger(__name__)


class ConfigWatcher:
    """Watch config file for changes and hot-reload strategy parameters."""

    def __init__(self, config_path: Path, on_change: Callable[[Settings], Awaitable[None]]):
        self._path = config_path
        self._on_change = on_change
        self._task: asyncio.Task | None = None

    async def watch(self) -> None:
        """Monitor file for changes using watchfiles.

        On each detected change:
        1. Reload config via load_settings()
        2. Validate the new config (Pydantic does this automatically)
        3. Call on_change with the new Settings instance
        4. If validation fails, log the error and keep running with old config
        """
        logger.info("config_watcher_started", path=str(self._path))
        async for _changes in awatch(self._path):
            logger.info("config_file_changed", path=str(self._path))
            # Debounce: wait for rapid successive changes to settle
            await asyncio.sleep(0.5)
            try:
                new_settings = load_settings(self._path)
            except ValidationError as exc:
                logger.error(
                    "config_reload_validation_failed",
                    path=str(self._path),
                    error_count=exc.error_count(),
                    errors=[
                        {
                            "field": " -> ".join(str(loc) for loc in err["loc"]),
                            "message": err["msg"],
                        }
                        for err in exc.errors()
                    ],
                )
                continue
            except Exception as exc:
                logger.error(
                    "config_reload_failed",
                    path=str(self._path),
                    error=str(exc),
                )
                continue

            await self._on_change(new_settings)
            logger.info("config_reloaded_successfully", path=str(self._path))

    def start(self) -> asyncio.Task:
        """Start watching in a background task. Returns the task."""
        self._task = asyncio.create_task(self.watch())
        return self._task

    def stop(self) -> None:
        """Cancel the watch task."""
        if self._task is not None:
            self._task.cancel()
            logger.info("config_watcher_stopped", path=str(self._path))
            self._task = None
