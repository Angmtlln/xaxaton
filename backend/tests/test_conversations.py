import asyncio

import pytest

from app.agent.conversations import ConversationStore, UnknownConversation, ConversationCapacityError


@pytest.mark.asyncio
async def test_ttl_expiration_and_capacity_eviction():
    store = ConversationStore(ttl_s=.01, max_conversations=1)
    async with store.session() as (first, _):
        pass
    async with store.session() as (second, _):
        pass
    with pytest.raises(UnknownConversation):
        async with store.session(first):
            pass
    await asyncio.sleep(.02)
    with pytest.raises(UnknownConversation):
        async with store.session(second):
            pass


@pytest.mark.asyncio
async def test_busy_conversation_not_evicted():
    store = ConversationStore(max_conversations=1)
    async with store.session():
        with pytest.raises(ConversationCapacityError):
            async with store.session():
                pass


@pytest.mark.asyncio
async def test_conversation_requests_are_serialized():
    store = ConversationStore()
    events = []
    async with store.session() as (cid, _):
        async def next_request():
            async with store.session(cid):
                events.append("second")
        waiter = asyncio.create_task(next_request())
        await asyncio.sleep(0)
        events.append("first")
        assert events == ["first"]
    await waiter
    assert events == ["first", "second"]
