"""Bounded process-local lifecycle around LangGraph checkpointed conversation state."""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain.agents import AgentState
from langgraph.checkpoint.memory import InMemorySaver


class ConversationState(AgentState):
    active_company: Optional[dict]


class UnknownConversation(ValueError):
    """The requested conversation expired or is not owned by this process."""


class ConversationCapacityError(RuntimeError):
    """Every session is currently in use; do not evict an active run."""


@dataclass
class _Lease:
    touched: float
    users: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConversationStore:
    """Leases and immutable per-thread model bindings around InMemorySaver."""

    def __init__(self, *, ttl_s: float = 1800, max_conversations: int = 100,
                 max_turns: int = 6):
        if ttl_s <= 0 or max_conversations < 1 or max_turns < 1:
            raise ValueError("Conversation limits must be positive")
        self.ttl_s = ttl_s
        self.max_conversations = max_conversations
        self.max_turns = max_turns
        self.checkpointer = InMemorySaver()
        self._leases: dict[str, _Lease] = {}
        self._master_models: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def pin_master_model(self, conversation_id: str, binding: Any) -> Any:
        """Keep the model/provider chosen for the first turn for this thread."""
        return self._master_models.setdefault(conversation_id, binding)

    @asynccontextmanager
    async def session(self, conversation_id: Optional[str] = None):
        async with self._lock:
            now = time.monotonic()
            expired = [key for key, item in self._leases.items()
                       if not item.users and now - item.touched >= self.ttl_s]
            for key in expired:
                await self.checkpointer.adelete_thread(key)
                del self._leases[key]
                self._master_models.pop(key, None)
            if conversation_id is not None:
                lease = self._leases.get(conversation_id)
                if lease is None:
                    raise UnknownConversation(conversation_id)
            else:
                if len(self._leases) >= self.max_conversations:
                    idle = [(item.touched, key) for key, item in self._leases.items()
                            if not item.users]
                    if not idle:
                        raise ConversationCapacityError()
                    _, evicted = min(idle)
                    await self.checkpointer.adelete_thread(evicted)
                    del self._leases[evicted]
                    self._master_models.pop(evicted, None)
                conversation_id = str(uuid.uuid4())
                lease = _Lease(touched=now)
                self._leases[conversation_id] = lease
            lease.users += 1
        try:
            async with lease.lock:
                yield conversation_id, lease
        finally:
            async with self._lock:
                lease.users -= 1
                lease.touched = time.monotonic()
