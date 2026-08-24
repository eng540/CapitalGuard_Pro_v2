import asyncio

import pytest

from capitalguard.interfaces.api.main import _register_background_task, app


@pytest.mark.asyncio
async def test_register_background_task_tracks_and_removes_cancelled_task():
    app.state.background_tasks.clear()

    async def long_running_task():
        await asyncio.Event().wait()

    task = asyncio.create_task(long_running_task())
    _register_background_task(task)

    assert task in app.state.background_tasks

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert task not in app.state.background_tasks
