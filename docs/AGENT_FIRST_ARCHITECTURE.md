# Agent-first архитектура продукта проверки контрагентов

## 1. Главная идея

Продукт становится **agent-first**.

Пользователь не начинает с формы поиска по ИНН и не обязан сначала получать полный отчёт. Основной интерфейс — чат с AI-аналитиком по контрагентам.

Сценарий:

```text
User → Chat → Master Agent → Tools / Capabilities → Structured Response → Rich UI
```

Пользователь может писать естественные запросы:

- «Проверь ООО Ромашка»
- «Какая у них прибыль за последние три года?»
- «Что у них с судами?»
- «Можно ли дать им отсрочку 60 дней?»
- «Сравни этих трёх поставщиков»
- «Кого безопаснее выбрать?»

Главный принцип: **снаружи агент выглядит универсальным, внутри он работает через ограниченный и качественный набор инструментов**.

---

## 2. Что сохраняем из текущего проекта

Не переписывать работающее ядро без необходимости.

Существующие:

- data providers;
- API-клиенты;
- парсеры;
- модели данных;
- финансовый анализ;
- юридический анализ;
- procurement-анализ;
- другие субагенты;
- pipeline полного отчёта;

нужно превращать в **domain capabilities / tools**, которыми пользуется Master Agent.

Субагенты остаются implementation detail. Пользователь напрямую с ними не взаимодействует.

---

## 3. Архитектура верхнего уровня

```text
Frontend
   ↓
Chat API / Streaming
   ↓
LangChain create_agent
   ↓
LangGraph execution runtime (под капотом LangChain)
   ↓
LangChain tool adapter
   ↓
Domain Tool Registry
   ↓
Domain Capabilities
   ↓
Existing Services / Subagents / Data Providers
```

Отдельный поток представления:

```text
Master Agent
   ↓
AssistantResponse
   ↓
UIBlock[]
   ↓
Frontend Renderer
```

---

## 4. Master Agent

Master Agent — единственная точка общения пользователя с системой.

Он отвечает за:

- понимание intent пользователя;
- определение компаний и сущностей;
- ведение conversation context;
- выбор минимально необходимого набора tools;
- вызов capabilities;
- объединение результатов;
- формирование ответа;
- выбор UI blocks для отображения.

Master Agent не должен:

- иметь arbitrary code execution;
- генерировать React/HTML/SVG;
- напрямую зависеть от конкретных API/data providers;
- получать огромные сырые JSON, если domain layer может их нормализовать;
- выдумывать company-specific факты при отсутствии данных;
- запускать полный анализ на каждый вопрос.

---

## 5. Domain capabilities / tools

Не делать десятки микротулов. Для MVP достаточно примерно 10–15 нормальных capabilities.

Пример:

```text
resolve_company(query)
get_company_profile(company)
get_financial_data(company, period?)
get_legal_data(company, filters?)
get_enforcement_data(company)
get_procurement_data(company)
get_external_risk_data(company)
analyze_financials(company)
analyze_legal_risks(company)
full_company_check(company)
compare_companies(companies, priorities?)
assess_deal_risk(company, deal_context)
```

Внутри tool может находиться обычный код, API-запрос или существующий LLM-субагент. Master Agent этого знать не должен.

---

## 6. Full company check

Текущий pipeline полного отчёта не удаляем.

Он становится capability:

```text
full_company_check(company)
```

Этот tool используется только для широких запросов вроде:

- «Проверь компанию»
- «Дай полный анализ»
- «Какие основные риски?»

Если пользователь спрашивает только выручку или суды, запускать полный анализ нельзя.

Полный check должен возвращать структурированный объект, а не только готовый markdown/html.

---

## 7. Conversation / company context

Нужно хранить сущности, о которых идёт разговор.

Пример:

```text
User: Проверь Сбер
→ resolve_company("Сбер")
→ active company = ПАО Сбербанк

User: А что у них с судами?
→ legal tool для active company
```

Минимальные модели:

```text
CompanyRef
ConversationState
ConversationStore
```

`ConversationStore` должен быть интерфейсом. Для MVP можно начать с in-memory implementation, позже заменить на Postgres без изменения Agent Runtime.

---

## 8. Tool contract

Все tools должны иметь единый контракт.

Domain contracts остаются framework-agnostic: LangChain adapter экспортирует
их модели, но `ToolResult`, `Evidence` и executors не наследуются от LangChain
и не зависят от состояния LangGraph.

```text
Tool:
  name
  description
  input_schema
  execute(context, args) -> ToolResult
```

Пример `ToolResult`:

```text
status: success | partial | error
data: structured payload
evidence: Evidence[]
warnings: string[]
freshness?: metadata
```

Не отдавать Master Agent огромные сырые ответы внешних API.

---

## 9. Evidence / источники

Все значимые выводы должны быть связаны с источниками.

Минимальная модель:

```text
Evidence:
  id
  source
  title
  url?
  observed_at?
  fetched_at?
  excerpt?
  metadata?
```

Аналитические находки:

```text
Finding:
  id
  domain
  title
  explanation
  evidence_ids[]
```

В новых agent-first контрактах не добавлять `severity`, domain risk level или
скрытую классификацию риска. Приоритет и смысл находки объясняются через
проверенные факты, детерминированные стоп-факторы и явную интерпретацию. Также
не использовать красивые числовые risk scores вроде `8.7/10`: это создаёт
псевдоточность и нарушает границу между фактами и AI-интерпретацией.

---

## 10. Deal risk

Отдельный first-class сценарий:

```text
assess_deal_risk(company, deal_context)
```

`deal_context` может содержать:

```text
amount
currency
prepayment_percent
payment_delay_days
contract_type
priorities
freeform_context
```

Важно отличать общий риск компании от риска конкретной сделки.

---

## 11. Comparison

Сравнение компаний должно быть отдельной capability:

```text
compare_companies(companies, priorities)
```

Не генерировать N полных отчётов.

Возвращать нормализованный comparison result:

```text
companies[]
dimensions[]
comparison_rows[]
strengths[]
weaknesses[]
recommendation?
evidence[]
```

Приоритеты пользователя должны влиять на сравнение.

---

## 12. Agent runtime

Master Agent harness строится на high-level `langchain.agents.create_agent`.
Он отвечает за native tool-calling loop, а LangGraph используется как
underlying execution/state runtime, который создаёт сам LangChain:

```text
create_agent
  → ChatGroq model node
  → allowlisted LangChain tool adapter
  → собственный domain Tool Registry
  → ToolResult artifact
```

Application boundary вокруг `create_agent` сохраняет:

- deterministic preflight до model/tool execution;
- model-call, tool-call и recursion budgets;
- tool timeout;
- локальную schema validation;
- structured errors;
- общий deadline и deterministic fallback;
- backend-controlled hydration `AssistantResponse`.

Raw `StateGraph`, собственные nodes и отдельный параллельный tool loop не нужны.
Их можно вводить только если позже появится действительно сложный
deterministic/stateful workflow, который нельзя выразить `create_agent` и
middleware. Независимые read-only tools можно выполнять параллельно только
когда это даёт измеримую пользу и не нарушает execution limits.
LangSmith не является обязательным runtime-сервисом: текущий срез не требует
его API key или tracing-конфигурации. Multi-turn срез использует встроенный
InMemorySaver для временного состояния диалога.

---

## 13. LLM integration

Есть ограничение на модели: Qwen / GPT-OSS.

Master Agent использует официальный `langchain-groq` / `ChatGroq` и native
tool calling внутри `create_agent`. Provider-specific настройки остаются в
runtime integration boundary. Текущие доменные LLM-вызовы и summary продолжают
использовать существующий `GroqClient.complete_json()`; migration Master Agent
не является причиной переписывать аналитический pipeline.

Если native tool calling недоступен, нарушает schema или не вызывает ожидаемый
tool для очевидного full-check/finance/legal запроса, application boundary выполняет
детерминированный fallback с уже проверенным ИНН. Модель не определяет verified
числа, evidence, URL или chart series.

---

## 14. Master prompt

System prompt Master Agent должен быть отдельным versioned файлом.

Основные правила:

- использовать минимально необходимое количество tools;
- не выдумывать факты;
- narrow question → targeted tool;
- broad due diligence → `full_company_check`;
- использовать active company context;
- учитывать priorities пользователя;
- отделять факты от интерпретации и рекомендации;
- показывать uncertainty;
- опираться на evidence;
- не раскрывать chain-of-thought;
- не показывать внутреннюю структуру субагентов;
- не генерировать неподдерживаемые UI-компоненты.

---

## 15. Structured AssistantResponse

Frontend не должен получать произвольную LLM-разметку.

Пример:

```text
AssistantResponse:
  message
  leading_artifact?  # company_summary после full_company_check
  blocks[]
  evidence[]
  suggested_actions?
  metadata?
```

`message` — основной conversational ответ. `leading_artifact` — компактная
детерминированная сводка компании перед текстом, только после full check.
`blocks` — выборочные вспомогательные визуальные элементы после текста.
Источники показываются компактно и свёрнуты по умолчанию. Узкие вопросы не
повторяют сводку компании и не разворачивают страницу отчёта внутри чата.

---

## 16. UI Blocks

Для MVP достаточно ограниченного набора компонентов:

```text
text
company_card
risk_summary
metric_grid
table
line_chart
bar_chart
finding_list
comparison_table
evidence_list
```

Master Agent может предложить только allowlisted тип блока. Все verified числа,
evidence, URL и chart series гидратируются backend из `ToolResult` и повторно
валидируются. Frontend полностью контролирует визуальный рендеринг.

Никакого arbitrary HTML/CSS/JS от модели.

Для графиков backend передаёт проверенные данные, например:

```text
LineChartBlock:
  type
  title
  x_axis
  series[]
  unit?
```

---

## 17. Frontend

Первый экран — максимально простой agent-first entry point:

```text
AI-аналитик контрагентов

[ Спросите что угодно о контрагенте... ]

Примеры:
Проверить компанию
Сравнить поставщиков
Оценить риск сделки
```

После первого сообщения открывается chat workspace.

Нужно поддержать:

- сообщения;
- rich UI blocks;
- company context chips;
- charts;
- tables;
- comparison;
- evidence/source drawer;
- loading/tool statuses;
- follow-up вопросы;
- ошибки и partial results.

---

## 18. Streaming

Если текущий стек позволяет — использовать SSE.

Не вводить WebSocket только ради модности.

Пример событий:

```text
run_started
status
tool_started
tool_completed
assistant_delta
assistant_blocks
run_completed
error
```

Показывать пользователю понятные статусы:

- «Ищу компанию»
- «Проверяю финансы»
- «Анализирую судебные дела»
- «Сравниваю компании»

Не показывать chain-of-thought.

---

## 19. Cache

Нужна cache boundary, но необязательно сразу Redis.

Интерфейс:

```text
CacheStore
```

Кэшировать дорогие read-only запросы по компании и доменам.

Ключ должен учитывать:

- company id;
- capability;
- параметры;
- при необходимости версию.

---

## 20. Errors

Tool не должен ронять весь агент сырой ошибкой.

Нормализованный формат:

```text
status: error
error_code
user_safe_message
retryable
diagnostics // только server-side
```

Master Agent должен уметь вернуть partial answer, если часть источников недоступна.

---

## 21. Observability

Даже для MVP логировать каждый agent run:

```text
run_id
conversation_id
model
iterations
tool_calls
tool_latency
model_latency
errors
total_duration
token_usage?
```

Не тащить тяжёлую observability платформу без необходимости.

---

## 22. Security boundaries

Master Agent работает только через allowlist tools.

Нельзя давать:

- arbitrary shell;
- arbitrary SQL;
- arbitrary Python execution;
- прямой доступ к секретам;
- произвольный fetch любых URL без контроля.

Tool input валидируется schema validator'ом.
External content считать untrusted.

---

## 23. Предлагаемая структура кода

Не копировать буквально, если текущий repo устроен иначе.

```text
backend/
  agent/
    runtime
    langchain_tools
    master_prompt
    models
    context
    tool_registry
    llm

  capabilities/
    company
    finance
    legal
    enforcement
    procurement
    external_risk
    full_check
    comparison
    deal_risk

  domain/
    models
    evidence
    findings

  infrastructure/
    providers
    persistence
    cache

  api/
    chat

frontend/
  features/
    chat/
    assistant-blocks/
```

---

## 24. Что не делать сейчас

Не добавлять без конкретной необходимости:

- raw LangGraph `StateGraph` и custom graph nodes;
- CrewAI;
- AutoGen;
- vector DB;
- RAG framework;
- Kafka;
- microservices;
- agent swarm;
- planner/critic/judge agents;
- self-reflection loops;
- dynamic React generation;
- arbitrary code execution.

LangChain `create_agent` уже является выбранным Master Agent harness. Если
предлагается другой или дополнительный framework, сначала должна быть
сформулирована конкретная проблема, которую он решает.

---

## 25. MVP scope

Обязательно:

1. Master Agent runtime.
2. LangChain `create_agent` + `ChatGroq` для Qwen/GPT-OSS.
3. Tool Registry.
4. Wrappers над существующими capabilities.
5. Company conversation context.
6. `full_company_check` как tool.
7. Targeted finance/legal запросы.
8. `compare_companies`.
9. Structured `AssistantResponse`.
10. UIBlock renderer.
11. Text/table/metric/chart/risk/evidence blocks.
12. Chat frontend.
13. Evidence display.
14. Basic errors.
15. Basic logging.
16. Streaming — если легко ложится в текущий стек.

Не обязательно для MVP:

- semantic long-term memory;
- background job infrastructure;
- granular RBAC;
- billing;
- multi-tenant platform;
- distributed agents.

---

## 26. Основные acceptance flows

### Flow A — полный анализ

```text
User: Проверь ООО X
→ resolve company
→ full_company_check
→ structured summary
→ risks
→ metrics
→ evidence
```

### Flow B — точечный финансовый вопрос

```text
User: Какая выручка у ООО X за последние три года?
→ resolve company
→ financial data only
```

`full_company_check` не должен запускаться.

### Flow C — follow-up

```text
User: А с судами что?
→ использовать active company
→ legal capability
```

### Flow D — сравнение

```text
User: Сравни ООО A, ООО B и ООО C. Главное — финансовая устойчивость.
→ resolve companies
→ targeted analyses
→ compare_companies
→ table/chart
→ explainable recommendation
```

### Flow E — риск сделки

```text
User: Контракт 15 млн, аванс 30%, остаток через 60 дней. Рискованно?
→ active company
→ relevant capabilities
→ assess_deal_risk
→ вывод относительно конкретной сделки
```

### Flow F — partial failure

Если один provider падает, весь conversation не должен падать.
Агент возвращает доступную часть результата и сообщает, чего не удалось получить.

---

## 27. Тесты

Минимум unit tests на:

- tool schema validation;
- Tool Registry;
- company context;
- AssistantResponse schema;
- UIBlock schema;
- error normalization;
- conversation state;
- routing behaviour.

Integration tests с mock LLM:

- finance question не вызывает full check;
- broad request вызывает full check;
- follow-up использует active company;
- comparison вызывает comparison capability.

Не завязывать основные тесты на реальный LLM/API.

---

## 28. Порядок разработки

1. Изучить repo и составить карту текущей архитектуры.
2. Определить, что уже есть и что можно переиспользовать.
3. Ввести domain contracts: `CompanyRef`, `Evidence`, `Finding`, `ToolResult`, `AssistantResponse`, `UIBlock`.
4. Обернуть существующие сервисы в capabilities/tools.
5. Проверить tools независимо от LLM.
6. Добавить Tool Registry.
7. Добавить Master Agent runtime через LangChain `create_agent`.
8. Добавить company/conversation context.
9. Подключить chat API.
10. Сделать один end-to-end сценарий.
11. Сделать frontend chat и UIBlock renderer.
12. Подключить full check.
13. Добавить comparison.
14. Добавить deal-risk.
15. Добавить streaming/evidence UX.
16. Прогнать acceptance scenarios и тесты.

Главное: **никакого Big Bang rewrite**.

Работать небольшими этапами и после каждого запускать доступные tests/typecheck/lint/build.

---

## 29. MVP vs production vs overengineering

### Быстрый MVP

- один Master Agent;
- 10–15 tools;
- существующие subagents как implementation detail;
- LangChain `create_agent` без raw graph;
- structured responses;
- chat;
- evidence;
- несколько UI blocks.

### Production evolution

Позже можно добавить:

- persistent conversations;
- более серьёзный cache;
- evals;
- tracing;
- cost control;
- permissions;
- audit log;
- queues/background jobs;
- richer report/artifact system.

### Переусложнение сейчас

- swarm агентов;
- planner + executor + critic + judge;
- self-reflection;
- raw/custom graph и дополнительные workflow frameworks;
- микросервисы;
- event-driven architecture;
- vector DB без конкретного retrieval use case.

---

## 30. Definition of Done

Архитектура считается реализованной достаточно хорошо для MVP, если:

- пользователь начинает работу прямо с chat input;
- Master Agent понимает company context;
- точечные вопросы используют точечные tools;
- широкий запрос запускает full check;
- текущие domain capabilities переиспользуются;
- работает сравнение компаний;
- работают follow-up вопросы;
- ответы связаны с evidence;
- UI строится из structured blocks;
- charts не генерируются произвольным кодом модели;
- падение одного tool не ломает весь run;
- модель заменяема через LangChain chat-model integration;
- Qwen/GPT-OSS не зашиты в application logic;
- нет самописного дублирующего tool loop или raw LangGraph без необходимости;
- основные сценарии покрыты тестами.

---

# Коротко

Не строим «магического суперагента».

Строим:

```text
Master Agent
+
ограниченные domain tools
+
существующее аналитическое ядро
+
conversation context
+
evidence
+
structured rich UI
```

Chat становится основным интерфейсом продукта, а отчёт, comparison, финансовый анализ, legal-анализ и deal-risk — capabilities внутри агента.

## Реализованный multi-turn срез

Временное состояние работает через LangChain AgentState и InMemorySaver, без
PostgreSQL history. Один conversation_id хранит одну active_company и последние
завершённые turns; company сохраняется только из результата backend tool.

Нормальный путь: Master → один разрешённый domain tool → compact ToolResult →
второй шаг Master → backend hydration. Master выбирает и упорядочивает finding_ids;
произвольный prose модели не публикуется. Backend сохраняет значения, evidence,
графики, required findings и gaps. Такой ограниченный synthesis не является
произвольной аналитической беседой или расчётом новых метрик.

Полная проверка тоже использует post-tool synthesis по компактным наблюдениям.
Backend автоматически ставит `company_summary` перед conversational текстом,
затем добавляет только выбранные допустимые артефакты. Стоп-факторы и пробелы
данных сохраняются независимо от выбора модели; malformed synthesis даёт
grounded fallback в том же conversational формате. Отдельный `/report`
открывается второстепенной ссылкой «Полный анализ».

Targeted get_financial_data и get_legal_data используют существующие builders,
не вызывая run_check. Полный анализ и /report сохранены.
Операционные границы и пример API: [MULTI_TURN_CHAT.md](MULTI_TURN_CHAT.md).
