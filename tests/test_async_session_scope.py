import asyncio

import pytest

from capitalguard.infrastructure.db.uow import SessionScoped


@pytest.mark.asyncio
async def test_session_scope_isolated_per_asyncio_task():
    SessionScoped.remove()
    parent_session = SessionScoped()

    async def child_session_identity():
        child_session = SessionScoped()
        try:
            return id(child_session), child_session is parent_session
        finally:
            SessionScoped.remove()

    child_id, shares_parent = await asyncio.create_task(child_session_identity())

    try:
        assert shares_parent is False
        assert child_id != id(parent_session)
        assert SessionScoped() is parent_session
    finally:
        SessionScoped.remove()


# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
