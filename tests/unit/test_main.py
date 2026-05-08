"""Tests for src.main entry point."""

import asyncio
import inspect

import pytest


@pytest.fixture
def _reset_shutdown_event():
    """Reset the module-level shutdown event between tests."""
    from src.main import _shutdown_event

    _shutdown_event.clear()
    yield
    _shutdown_event.clear()


@pytest.mark.usefixtures("_reset_shutdown_event")
async def test_async_main_shuts_down_on_event():
    """async_main exits cleanly when the shutdown event is set."""
    from src.main import _shutdown_event, async_main

    # Set the shutdown event after a short delay so async_main doesn't block forever
    async def trigger_shutdown():
        await asyncio.sleep(0.05)
        _shutdown_event.set()

    asyncio.create_task(trigger_shutdown())
    await async_main()  # Should return without error


def test_main_function_is_sync():
    """main() is a synchronous callable (required by pyproject.toml scripts entry)."""
    from src.main import main

    assert callable(main)
    assert not inspect.iscoroutinefunction(main)


def test_async_main_is_coroutine():
    """async_main() is an async function."""
    from src.main import async_main

    assert inspect.iscoroutinefunction(async_main)
