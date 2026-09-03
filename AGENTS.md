# Project Context

Мы создаём AI-агента для проверки контрагентов в банковском продукте.

Цель продукта: помочь пользователю анализировать отчёты о компаниях, находить риски и отвечать на вопросы только на основе доступных входных данных.

Критерии успеха:

- отсутствие hallucination;
- использование только входных данных;
- объяснимость выводов;
- ответы на вопросы пользователя.

## Data

Источник данных: `GetFullReportResponse` JSON.

Основные блоки:

- `baseInfo`;
- `status`;
- `foundersInfo`;
- `relatedCompanies`;
- `kindsOfActivityInfo`;
- `finReports`;
- `coefficient`;
- `arbitrationCases`;
- `arbitrationByStatus`;
- `executionProceedings`;
- `inspections`;
- `licenses`;
- `procurements`;
- `reputationalRisks`.

JSON является source of truth. CSV — только flattened-представление JSON и не должен определять доменную модель.

---

# Risk Model

В данных есть два risk-поля.

`baseInfo.riskLevel`:

- `LOW`;
- `MEDIUM`;
- `HIGH`;
- `UNKNOWN`.

`zskRiskLevel`:

- `GREEN`;
- `YELLOW`;
- `RED`.

Это отдельные банковские оценки. Их нельзя считать взаимозаменяемыми или молча объединять в один уровень риска.

Важно: ZSK не является полной оценкой благонадёжности компании.

По словам кейсодателя, ZSK рассчитывается по банковским данным:

- движение денежных средств;
- операции с физическими лицами;
- вывод наличных;
- другие банковские признаки.

ZSK не учитывает часть открытых данных, например судебные дела.

Поэтому система должна строить дополнительный AI risk profile поверх отчёта, не заменяя и не переименовывая банковские risk-поля.

---

# Architecture Principles

```text
Raw JSON
↓
Normalization Layer
↓
Business Domains
↓
Deterministic Analytics
↓
Risk Signals
↓
LLM Interpretation
```

Правила:

- LLM не анализирует сырой JSON напрямую.
- LLM не должен сам искать риски среди тысяч полей.
- Код отвечает за извлечение фактов и расчёты.
- LLM отвечает за объяснение, связывание подготовленных фактов и диалог с пользователем.

---

# Business Domains

Данные должны быть нормализованы в 10 бизнес-блоков:

1. Bank Risk.
2. Company Identity.
3. Ownership & Management.
4. Related Companies.
5. Business Profile.
6. Financial Health.
7. Legal Risks.
8. Enforcement Risks.
9. Compliance & Reputation.
10. Procurement.

---

# Engineering Constitution

1. Не создавать сложность без необходимости.
2. Не использовать LLM там, где достаточно deterministic logic.
3. Не создавать общий risk score от 0 до 100 без обоснованной методологии.
4. Каждый вывод должен иметь evidence со ссылкой на исходные факты или расчёты.
5. Разделять:
   - raw facts;
   - calculated metrics;
   - risk signals;
   - AI explanations.
6. Не доверять слепо `reputationalRisks`: проверять их на конфликты с raw data и сохранять оба слоя.
7. Не добавлять технологии без конкретной необходимости.
8. При неопределённости явно фиксировать принятое решение, допущение и trade-off.
9. Код должен быть простым, читаемым, тестируемым и расширяемым.

---

# Current Phase

Текущий этап: **Data modeling and normalization**.

Первый приоритет: создать нормализованную бизнес-модель данных из исходного `GetFullReportResponse` JSON.

До завершения этого этапа и явного подтверждения не начинать:

- UI;
- chatbot;
- MCP;
- сложные agent workflows.
