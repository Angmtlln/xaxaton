# Agent-First Architecture

## 1. Product goal

The product is an **AI counterparty analyst**, not a web-rendered report.

The core job is:

> Move the cognitive load of collecting, reading, reconciling and interpreting counterparty information from the user to the agent.

The user should be able to arrive with a business question such as:

- `Проверь этого контрагента.`
- `Что здесь самое опасное?`
- `Почему это важно?`
- `Мне предлагают отсрочку 60 дней — что думаешь?`
- `Сравни этих поставщиков и объясни, кого выбрать.`

The agent decides which data and capabilities are needed, reasons over verified information, explains the result and shows evidence.

## 2. Product interaction model

### Primary interface: conversation

The chat is the product.

```text
User need
  ↓
Master Agent
  ↓
relevant tools / verified data
  ↓
reasoning
  ↓
conversational answer
  + optional inline artifacts
```

The old model:

```text
INN → full report → user reads it → user draws conclusions
```

is no longer the primary UX.

### Full check response

For a broad request such as:

`Проверь контрагента <ИНН>`

the response may be:

```text
compact deterministic company_summary
↓
Master conversational synthesis
↓
0..N relevant inline artifacts
↓
compact/collapsed evidence
↓
optional "Полный анализ"
```

`company_summary` is a stable context artifact produced from verified backend data. It is not repeated on ordinary follow-up turns.

The legacy full report remains a secondary drill-down artifact.

## 3. High-level architecture

```text
Frontend chat workspace
        ↓
Chat API
        ↓
Master Agent (LangChain create_agent)
        ↓
LangGraph execution/state runtime
        ↓
Tool / capability layer
        ↓
Normalized verified ToolResults
        ↓
Master reasoning / synthesis
        ↓
Grounding verification when required
        ↓
AssistantResponse
        ├─ natural-language message
        ├─ leading artifact (optional company_summary)
        ├─ inline artifacts
        ├─ evidence
        └─ suggested follow-ups
```

The orchestration framework is infrastructure, not the domain model.

## 4. Orchestration

Use:
- `langchain.agents.create_agent` as the high-level Master Agent harness;
- LangGraph as the underlying execution/state runtime.

Do not build raw `StateGraph` workflows unless the product later has real requirements such as:
- durable long-running execution;
- complex deterministic branching;
- human approval nodes;
- resume after long interruption;
- workflow-level recovery/checkpoints.

Current primary pattern:

```text
Master → tool(s) → ToolResult → Master → answer
```

Domain capabilities must not depend deeply on LangChain/LangGraph types.

## 5. Domain capability layer

The current analytical core is reused, not replaced.

Examples of capabilities:
- `full_company_check`
- company identity/profile
- finance
- legal/reliability
- enforcement
- procurement
- external signals
- comparison
- deal-context analysis

Existing specialized LLM agents (GPT-OSS/Qwen/etc.) may remain implementation details inside these capabilities.

The Master should not care whether a capability is implemented with deterministic Python, SQL/database access, external APIs, GPT-OSS, Qwen, or another LLM.

`run_check()` is wrapped as `full_company_check`; it is not the required path for every user request.

## 6. ToolResult design: verified data, not hardcoded reasoning

Domain tools should return **normalized verified observations** rather than a fixed catalog of conclusions.

Prefer data like:

```json
{
  "domain": "finance",
  "metrics": [
    {
      "name": "revenue",
      "period": 2025,
      "value": 748359000,
      "unit": "RUB",
      "evidence_ref": "..."
    }
  ],
  "series": [],
  "events": [],
  "coverage": {},
  "policy_signals": [],
  "evidence": []
}
```

over a large set of rules such as:

```text
if metric X has value Y → prewritten conclusion Z
```

### Provenance IDs

Fact/evidence IDs are technical references for:
- traceability;
- audit;
- grounding;
- source navigation;
- UI hydration.

They are **not a whitelist of conclusions** the Master is allowed to make.

A strong Master must be able to connect multiple verified observations and reason about their relevance to the user's context.

## 7. Three-layer decision model

### Layer A — verified facts/data

Backend-owned:
- metrics;
- events;
- statuses;
- company identifiers;
- court/procurement/enforcement records;
- time series;
- data coverage;
- provenance/evidence.

### Layer B — deterministic policy

Keep a small explicit deterministic layer only where the business/domain itself defines a rule.

Examples:
- official stop signals;
- bank-provided risk status;
- ZSK signal;
- explicit compliance policy.

A model must not override a deterministic hard stop by rhetorical reasoning.

### Layer C — Master reasoning

The Master decides:
- which observations are relevant;
- how multiple observations relate;
- why something matters;
- what information is missing;
- what the user should verify next;
- how the evidence affects this particular business context or deal.

Do not reduce Layer C to a hardcoded rule catalog.

## 8. Grounding and hallucination defense

The system should protect facts **without preventing reasoning**.

### 8.1 Structured truth boundary

LLMs do not own:
- identifiers;
- amounts;
- dates used as verified company data;
- metrics;
- chart series;
- evidence;
- source URLs;
- deterministic policy signals.

Backend validates and hydrates these values.

### 8.2 Conversation history is not factual memory

Keep two different concepts:

```text
message_history
    → helps understand the dialogue

trusted_context
    → contains verified structured company data
```

Recommended trusted context includes:
- active company;
- verified compact ToolResults/observations;
- evidence/provenance;
- user-supplied business/deal context;
- relevant last domain/topic.

A factual statement from a prior assistant message is not trusted merely because it is present in chat history.

This prevents hallucinations from compounding across turns.

### 8.3 Grounding verifier

For substantive company-specific analytical/recommendation answers, use a bounded grounding-verification step.

Verifier input:

```text
candidate Master answer
+
verified trusted context used for the answer
```

Verifier output should be narrow and structured, for example:

```json
{
  "supported": false,
  "unsupported_claims": ["..."]
}
```

The verifier checks whether concrete company-specific factual claims are unsupported by the provided verified context.

It does **not**:
- judge writing style;
- require specific Russian sentence templates;
- implement a regex grammar;
- replace the Master reasoning.

If unsupported claims are found:
1. allow at most one repair attempt;
2. if it still fails, use a conservative grounded fallback.

Do not create critic swarms or open-ended self-reflection loops.

### 8.4 Deterministic checks remain useful

Use deterministic validation for things that are actually deterministic:
- schema;
- known IDs;
- allowed tools;
- source URLs;
- company identifiers;
- evidence references;
- UI payloads;
- exact verified metrics when presented as trusted UI data.

Do not turn deterministic checks into an NLP entailment engine.

## 9. Natural-language synthesis

The Master is the author of the conversational answer.

It may:
- explain;
- interpret;
- connect observations;
- answer `Почему?`;
- answer `Объясни проще`;
- state uncertainty;
- ask useful clarifying questions;
- give cautious recommendations based on verified context.

Backend must not sentence-by-sentence rewrite or censor normal Russian prose with a large regex/word-list policy.

The backend may fall back to deterministic text when the model/provider or structured contract fails, but fallback is a safety net, not the normal user experience.

## 10. Conversation state

Conversation is a first-class entity.

Minimum useful state:

```text
conversation_id
active_company
message_history
trusted_context
user/business context
last relevant domain/topic (optional)
Master model/provider for the thread
```

Follow-ups such as:
- `Почему это плохо?`
- `Объясни проще`
- `Что это значит?`
- `Насколько это критично?`

must use the current conversation and trusted context.

They should not require another INN or explicit domain keyword.

Do not call a tool if the answer can be produced from already trusted context.

The Master model should be stable for the lifetime of a conversation unless the system explicitly starts a new thread/migration strategy.

## 11. Master model strategy

The architecture must be provider/model agnostic.

The Master model is a configuration choice and may be A/B-tested.

Stronger reasoning/conversation models such as GLM or DeepSeek may be used when they improve:
- natural dialogue;
- contextual follow-ups;
- tool routing;
- complex reasoning;
- grounded synthesis.

Existing GPT-OSS/Qwen models may continue as specialized domain agents.

Do not bake a single provider into the domain layer.

## 12. UI architecture

The assistant response is conversation-first.

Conceptually:

```text
AssistantResponse
├── message
├── leading_artifact?       # company_summary for full check
├── artifacts[]
├── evidence[]
├── notices[]
└── suggested_actions[]
```

Rules:
- text is primary;
- artifacts are secondary;
- evidence is compact/collapsible;
- charts/tables use backend-verified data;
- no arbitrary model-generated HTML/SVG/JS;
- `/report` remains a drill-down, not the default user journey.

## 13. Failure model

Use simple bounded failure handling:
- typed errors;
- timeouts;
- tool-call/recursion limits;
- PARTIAL / NO_DATA;
- deterministic fallback;
- one bounded grounding repair attempt.

Do not build heavyweight distributed reliability infrastructure for the hackathon.

## 14. What we deliberately do not build now

Unless a concrete requirement appears:
- no raw LangGraph graph for marketing;
- no custom LangGraph clone;
- no vector database;
- no generic RAG framework;
- no Redis/Kafka/message broker;
- no microservice split;
- no agent swarm;
- no planner/critic/judge chain;
- no semantic regex verifier;
- no arbitrary code execution;
- no dynamic React generation;
- no permanent semantic memory.

Engineering effort should go into agent behavior, reasoning quality, evidence and demo scenarios.

# Roadmap

## Stage 1 — Agent-first foundation — DONE
- chat API;
- Master Agent shell;
- `full_company_check`;
- reuse of `run_check()`;
- ToolResult/AssistantResponse;
- rich UI;
- legacy report preserved.

## Stage 2 — LangChain orchestration — DONE
- `LangChain create_agent`;
- LangGraph runtime;
- LangChain tool integration;
- live Groq tool-calling smoke;
- bounded execution and deterministic fallback.

## Stage 3 — Conversation-first UX / multi-turn — DONE, STABILIZING

Implemented:
- chat workspace;
- compact `company_summary`;
- full report as drill-down;
- active-company conversation basics;
- targeted finance/legal flows;
- inline artifacts.

Current stabilization priorities:
1. remove report-style synthesis;
2. remove hardcoded reasoning catalogs where they act as a conclusion whitelist;
3. remove semantic/lexical regex policing of natural prose;
4. keep trusted structured context separate from chat history;
5. let the Master reason over normalized verified data;
6. add bounded grounding verification;
7. A/B test a stronger Master model;
8. behavioral multi-turn evals.

Acceptance conversation:

```text
Проверь контрагента ...
→ А что с финансами?
→ Почему это плохо?
→ Объясни проще.
→ Насколько это критично для моей сделки?
→ А с судами?
→ Что там самое неприятное?
```

This must feel like one continuous analyst conversation, not seven report renders.

## Stage 4 — Multi-company comparison — NEXT

Support several company refs and requests such as:

`Сравни этих поставщиков. Главное — финансовая устойчивость и судебные риски.`

The Master should:
- collect only relevant domains;
- compare observations;
- account for user priorities;
- explain trade-offs;
- use compact comparison artifacts;
- avoid generating N separate full reports.

## Stage 5 — Deal-context reasoning

Use business context:
- role of the counterparty;
- amount;
- prepayment;
- payment delay;
- contract type;
- user priorities.

Goal:

Not merely:
`company has risk`

but:
`what these verified observations mean for this specific deal`.

## Stage 6 — Complex agent scenarios

Focus on behavior:
- ambiguous requests;
- missing data;
- conflicting signals;
- changed priorities;
- explaining a previous conclusion;
- deciding whether a new tool call is necessary;
- asking useful clarifying questions;
- targeted additional checks.

This is more valuable than adding infrastructure or many UI screens.

## Stage 7 — Evaluation, demo polish and deploy

- curated behavioral eval set;
- model A/B;
- groundedness;
- tool-call correctness;
- latency;
- graceful failures;
- remove technical terminology / AI-slop from UI;
- rehearse the 8-minute pitch;
- simple deployment.

## Product definition of done

The product is successful when a user can say:

> `Мне предлагают работать с этой компанией с отсрочкой 60 дней. Что думаешь?`

and the system:
1. understands the business goal;
2. asks for missing context only when useful;
3. chooses relevant capabilities;
4. reasons over verified data;
5. explains the important parts;
6. shows evidence;
7. maintains follow-up context;
8. reduces the need to manually read and reconcile a long counterparty report.
