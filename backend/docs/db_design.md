# Дизайн базы данных

PostgreSQL 15+. Схема лежит в [db/schema.sql](../db/schema.sql), справочник
кодов меток — в [db/seed_dictionary.sql](../db/seed_dictionary.sql).

Источник данных — `contractors_audit.snapshot.json`, дамп MongoDB с ответами
`GetFullReportResponse` (100 карточек контрагентов).

## 1. Три слоя

| Схема | Назначение | Кто пишет |
|---|---|---|
| `raw` | документ снапшота один-в-один, включая `$date` и `$numberLong` | загрузчик |
| `core` | нормализованные сущности отчёта | загрузчик |
| `audit` | прогоны агента: факты, ответы блочных агентов, summary, заземление | API |

Смысл разделения. `raw` — источник истины и страховка: если завтра поменяется
логика разбора, всё пересобирается из него без повторной выгрузки. `core` —
то, по чему можно считать выборки и строить витрины. `audit` — след работы
агента, по которому считаются критерии приёмки.

## 2. Ключевое решение: снапшот, а не «текущее состояние»

Отчёт по контрагенту — срез на дату. Поэтому дочерние таблицы висят не на
компании, а на `core.report_snapshots`:

```
core.companies (inn UNIQUE)
   └── core.report_snapshots (company_id, report_date)  UNIQUE (company_id, report_date)
         ├── phones, activity_codes, tax_systems, branches
         ├── founders, related_companies → parent_organizations
         ├── reputational_risks
         ├── arbitration_cases, arbitration_summary
         ├── execution_proceedings, inspections
         ├── fin_reports, fin_coefficients
         ├── licenses, procurements
         └── snapshot_coverage (паспорт полноты)
```

Повторная загрузка того же отчёта обновляет строку, новая дата отчёта создаёт
новую версию. История сравнения «было/стало» доступна без доработок.

## 3. Как блоки продукта легли на таблицы

Деление из [blocks_summary_design.md](../../blocks_summary_design.md), рабочая
схема из четырёх блоков.

| Блок | Таблицы |
|---|---|
| 1. Кто это | `report_snapshots` (baseInfo, status), `phones`, `activity_codes`, `tax_systems`, `branches`, `founders`, `related_companies`, `parent_organizations` |
| 2. Надёжность и правовые риски | `report_snapshots.risk_level` и `zsk_risk_level`, `reputational_risks`, `arbitration_cases`, `arbitration_summary`, `execution_proceedings`, `inspections` |
| 3. Финансовое состояние | `fin_reports`, `fin_coefficients` |
| 4. Опыт и позитивные сигналы | `procurements`, `licenses`, `reputational_risks` (POSITIVE) |

## 4. Решения, продиктованные данными

**Готовая проза отчёта хранится, но помечена как непригодная для выводов.**
`core.reputational_risks.name` — это текст банка. В 46 карточках из 100 он
противоречит цифрам той же карточки (гипотеза H4). Выводы строятся по `code`,
для чего заведён справочник `core.risk_code_dictionary` с полями `severity` и
`is_hard_stop`.

**Числа нормализуются на загрузке.** `$numberLong` и `$date` разворачиваются в
`numeric`/`date`, `executionProceedings[].amount` приходит строкой и пишется в
`numeric`. Отдельный флаг `amount_known` отличает «сумма 0» от «сумма не
раскрыта»: в выгрузке 770 записей из 3873 без суммы, и складывать их как ноль
нельзя.

**Полнота данных — таблица, а не вычисление на лету.**
`core.snapshot_coverage` хранит 9 булевых признаков и счётчик `filled_blocks`.
Треть карточек почти пустая, поэтому по этому полю нужны фильтрация и
сортировка (гипотеза H9, решение S6).

**Оценки банка — enum, а не текст.** `risk_level` и `zsk_risk_level` типизованы
и не пересчитываются. Свой скоринг продукт не считает.

**Одна строка на год.** `arbitration_cases` и `fin_reports` имеют
`UNIQUE (snapshot_id, year)`: в выгрузке встречаются повторы года, загрузчик
берёт первую запись.

## 5. Слой прогонов агента

| Таблица | Что хранит |
|---|---|
| `audit.snapshot_facts` | кэш детерминированных фактов по блоку и версии калькулятора |
| `audit.analysis_runs` | один проход по одному ИНН: модели, режим, токены, время, статус |
| `audit.run_blocks` | ответ каждого из четырёх агентов плюс то, что реально ушло ему на вход (`facts_input`) |
| `audit.run_summaries` | итог Summary-LLM: группа рекомендации, ключевые цифры, риски, пробелы |
| `audit.run_statements` | каждое утверждение агента и его ссылка на факт и поле карточки |
| `audit.expert_labels` | экспертная разметка утверждений для критериев приёмки |

`facts_input` хранится намеренно: без него нельзя воспроизвести, почему модель
ответила именно так, и нельзя отличить ошибку модели от ошибки калькулятора.

`calculator_ver` в `analysis_runs` и `snapshot_facts` фиксирует версию
детерминированного слоя. При изменении логики расчёта старые прогоны остаются
интерпретируемыми.

## 6. Витрины

| Представление | Зачем |
|---|---|
| `core.v_latest_snapshots` | последняя карточка по каждой компании, точка входа PoC |
| `audit.v_green_with_negatives` | H3: зелёный светофор при негативных метках |
| `audit.v_okved_contradictions` | H4: 10+ кодов ОКВЭД без метки `massOkved` |
| `audit.v_run_grounding` | доля утверждений со ссылкой на поле, критерии приёмки 1 и 2 |

Примеры запросов для проверки гипотез:

```sql
-- H3: сколько карточек GREEN при жёстких метках
SELECT count(*) FROM audit.v_green_with_negatives
 WHERE negative_codes && ARRAY['fnsBlocking','liquidationStatus','invalidAddress'];

-- H4: расхождения по ОКВЭД
SELECT count(*) FROM audit.v_okved_contradictions;

-- S6: распределение полноты
SELECT filled_blocks, count(*) FROM core.snapshot_coverage GROUP BY 1 ORDER BY 1;
```

## 7. Что сознательно не сделано

- Графа связей между компаниями нет как отдельной сущности: в выгрузке всего
  2 ребра, проверять гипотезу не на чем (S10).
- Хранилища пользовательских диалогов нет: память нужна только в рамках сессии.
- Скоринга и весов нет: продукт не считает свою оценку риска.
