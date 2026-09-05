"""Bounded process-local lifecycle around LangGraph checkpointed conversation state."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain.agents import AgentState
from langgraph.checkpoint.memory import InMemorySaver


class ConversationState(AgentState):
    active_company: Optional[dict]
    trusted_context: Optional[dict]
    # Сравнение хранится отдельно: trusted_context привязан к одной компании.
    comparison_context: Optional[dict]
    user_context: Optional[list[str]]
    last_topic: Optional[str]
    last_answer_verified: Optional[bool]


TRUSTED_DOMAIN_LIMIT = 45_000
TRUSTED_CONTEXT_LIMIT = 110_000


def merge_trusted_context(current: Optional[dict], observation: dict) -> dict:
    """Merge one backend-built observation without ever reading assistant prose."""
    if observation.get("schema_version") != "verified-context-1":
        raise ValueError("Unknown trusted context schema")
    domain = observation.get("domain")
    company = observation.get("company")
    if domain not in {"full_check", "finance", "legal"} or not isinstance(company, dict):
        raise ValueError("Invalid trusted context")
    inn = company.get("inn")
    encoded = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(inn, str) or len(encoded) > TRUSTED_DOMAIN_LIMIT:
        raise ValueError("Trusted domain context is invalid or too large")

    previous = current if isinstance(current, dict) else {}
    previous_company = previous.get("company") or {}
    if previous_company.get("inn") != inn or any(
        previous_company.get(key) is not None and company.get(key) is not None
        and previous_company[key] != company[key] for key in ("snapshot_id", "report_date")
    ):
        previous = {}
    domains = dict(previous.get("domains") or {})
    domains[domain] = json.loads(encoded)
    if domain == "full_check" and observation.get("sections"):
        for topic, prefixes, section_names in (
            ("finance", ("fin.",), {"finance_scope", "calculations", "coefficients"}),
            ("legal", ("court.", "execproc.", "inspections."), {"court_years", "court_stages", "proceedings", "inspections", "legal_aggregates"}),
        ):
            projected = json.loads(encoded)
            projected["domain"] = topic
            for group in ("metrics", "series", "events"):
                projected[group] = [item for item in projected[group] if item["id"].startswith(prefixes)]
            projected["sections"] = {key: value for key, value in projected["sections"].items()
                                     if key in section_names | {"source_dates", "available_sections", "data_gaps"}}
            projected["evidence"] = [item for item in projected["evidence"] if item["fact_id"].startswith(prefixes + ("bank.", "flags.", "company."))]
            domains[topic] = projected
    merged = {"company": dict(company), "domains": domains}
    if len(json.dumps(merged, ensure_ascii=False, separators=(",", ":"))) > TRUSTED_CONTEXT_LIMIT:
        # Preserve the new domain and the broad policy context, if available.
        domains = {domain: domains[domain], **(
            {"full_check": domains["full_check"]}
            if domain != "full_check" and "full_check" in domains else {}
        )}
        merged = {"company": dict(company), "domains": domains}
    return merged


def store_comparison_context(observation: dict) -> dict:
    """Проверенное наблюдение сравнения; прозу ассистента сюда не кладём."""
    if observation.get("schema_version") != "verified-context-1":
        raise ValueError("Unknown trusted context schema")
    companies = observation.get("companies")
    if observation.get("domain") != "comparison" or not isinstance(companies, list):
        raise ValueError("Invalid comparison context")
    if len(companies) < 2 or any(not isinstance(item, dict) or not item.get("inn")
                                 for item in companies):
        raise ValueError("Comparison context needs at least two identified companies")
    encoded = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > TRUSTED_CONTEXT_LIMIT:
        raise ValueError("Comparison context is too large")
    return json.loads(encoded)


def select_trusted_context(current: Optional[dict], topic: Optional[str]) -> Optional[dict]:
    if not isinstance(current, dict):
        return None
    domains = current.get("domains")
    company = current.get("company")
    if not isinstance(domains, dict) or not isinstance(company, dict):
        return None
    selected = domains.get(topic) if topic else None
    if selected is None and domains:
        selected = next(reversed(domains.values()))
    if not isinstance(selected, dict) or (selected.get("company") or {}).get("inn") != company.get("inn"):
        return None
    return selected


def with_related_domains(selected: dict, current: Optional[dict]) -> dict:
    """Deal reasoning may use other verified domains, never assistant prose."""
    related = {}
    for topic, context in ((current or {}).get("domains") or {}).items():
        if topic not in {"finance", "legal"} or topic == selected.get("domain"):
            continue
        if (context.get("company") or {}).get("inn") != (selected.get("company") or {}).get("inn"):
            continue
        related[topic] = {key: value for key, value in context.items() if key != "evidence"}
    return {**selected, "related_domains": related} if related else selected


def append_user_context(current: Optional[list[str]], message: str, *, limit: int = 4) -> list[str]:
    """Keep bounded user-supplied context separate from verified company facts."""
    values = [item[:1000] for item in (current or []) if isinstance(item, str) and item.strip()]
    if message.strip():
        values.append(message.strip()[:1000])
    return values[-limit:]


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
