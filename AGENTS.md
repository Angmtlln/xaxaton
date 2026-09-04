# AGENTS.md

## Product direction

This repository is an **agent-first, conversation-first counterparty analysis product**.

The primary product is the conversation with the Master Agent. A full report is a secondary drill-down artifact, not the main interaction model.

The product goal is to **move the cognitive load of reading and reconciling counterparty data from the user to the agent**.

The detailed architecture and roadmap live in `docs/AGENT_FIRST_ARCHITECTURE.md`.

## Core architecture rules

- Master Agent orchestration uses `langchain.agents.create_agent`; LangGraph is the underlying execution/state runtime.
- Do not introduce raw `StateGraph`, another agent framework, or a custom workflow engine unless a concrete workflow requirement needs it.
- Keep domain capabilities and contracts framework-independent.
- Reuse the existing analytical pipeline, facts, providers, grounding, evidence, and legacy report flow where useful.
- `run_check()` is a domain capability behind `full_company_check`, not the architecture of the whole product.
- Narrow questions must use targeted capabilities instead of running a full check.

## Conversation-first behavior

- The Master Agent's natural-language answer is the primary content of an assistant turn.
- UI artifacts are secondary and only support the current explanation.
- A full company check may prepend one deterministic compact `company_summary`.
- Do not render a full dashboard/report inside every assistant message.
- Follow-ups such as `Почему?`, `Объясни проще`, `Что это значит?` and `Насколько это критично?` must use conversation context and should not require new tools when existing trusted context is sufficient.
- The agent may ask for business context when it materially changes the answer, but must not turn every conversation into a questionnaire.

## Verified data vs reasoning

Treat these layers differently.

### STRICT: backend-owned and verifiable

The backend owns and validates:
- company identifiers;
- tool arguments;
- normalized structured data;
- metrics and numeric series;
- evidence/provenance references;
- source URLs;
- UI artifacts;
- NO_DATA / PARTIAL state;
- deterministic policy signals such as official hard stops.

These values must not be invented by an LLM.

### CONTROLLED: agent choices

The Master may choose:
- which tools to call;
- which verified observations matter for the user's goal;
- which artifacts are useful;
- whether more data is required.

These choices remain bounded by schemas, allowlists, tool limits, and current trusted context.

### FREE: natural-language reasoning

The Master is allowed to:
- explain;
- interpret;
- connect verified observations;
- answer follow-ups;
- simplify previous explanations;
- reason about relevance to the user's business context;
- state cautious recommendations.

Do **not** build a semantic/lexical verifier for Russian prose with regexes, word lists, allowed sentence forms, or grammar heuristics.

Grounding applies to structured data and provenance. Natural-language reasoning is evaluated with behavioral/live tests and a bounded grounding-verification step where appropriate.

## Provenance IDs are not a reasoning whitelist

Fact/evidence identifiers exist for traceability, grounding, audit, and UI hydration.

They must **not** become a hardcoded catalog of all conclusions the Master is allowed to make.

Domain tools should prefer normalized verified data, events, metrics, coverage and provenance over prewritten conclusions.

Keep deterministic conclusion rules only where the business/domain itself defines a deterministic policy.

## Trusted conversation context

Natural-language message history is useful for conversational continuity but is **not a trusted factual database**.

Keep structured trusted context separately from assistant prose, including:
- active company;
- verified ToolResults or compact normalized observations;
- relevant evidence/provenance;
- current business/deal context;
- last relevant domain/topic when useful.

A previous assistant statement must not become true merely because the model said it in an earlier turn.

## Hallucination defense

Prefer layered controls:
1. structured schemas and tool allowlists;
2. verified normalized ToolResults;
3. provenance/evidence checks;
4. exact validation for backend-owned identifiers, URLs and UI values;
5. deterministic policy signals;
6. bounded grounding verification for substantive company-specific reasoning;
7. at most one repair pass before conservative fallback.

Do not attempt to prove semantic entailment of every sentence with handcrafted regex logic.

## UI boundary

- The LLM does not generate HTML, SVG, arbitrary JS, source URLs or trusted chart data.
- Frontend renders only allowlisted artifacts.
- Backend hydrates metrics, charts, links and evidence from verified data.
- Master explains what the verified data means.

## Engineering scope

Avoid infrastructure and abstraction without a concrete requirement:
- no vector DB by default;
- no Redis/Kafka/message broker by default;
- no microservices split for the hackathon;
- no agent swarm / planner-critic-judge architecture;
- no semantic regex engine;
- no arbitrary code execution;
- no duplicate domain layer beside the existing one.

Prefer a small extension of current boundaries over a big rewrite.

## Compatibility

Until explicitly changed:
- preserve `/api/v1/checks`;
- preserve `/report`;
- preserve existing domain pipeline behavior and auditability.

## Testing priorities

Prefer behavioral tests over implementation-detail tests for agent behavior.

Important scenarios include:
- correct targeted tool selection;
- no unnecessary tool call for contextual follow-ups;
- continuity across `Почему?` / `Объясни проще`;
- grounded company-specific reasoning;
- NO_DATA / PARTIAL;
- provider/model failure and fallback;
- legacy endpoint regression.

When a large task has genuinely independent workstreams, subagents may be used selectively. Do not use them mechanically.
