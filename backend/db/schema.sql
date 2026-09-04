-- =====================================================================
--  Контрагент-агент. Схема БД (PostgreSQL 15+)
--  Источник данных: contractors_audit.snapshot.json (дамп MongoDB,
--  ответ GetFullReportResponse банковского отчёта по контрагенту).
--
--  Три слоя:
--    1. raw     — сырой снапшот как пришёл (jsonb), источник истины;
--    2. core    — нормализованные сущности отчёта (одна строка снапшота
--                 = одна версия отчёта по компании на дату);
--    3. audit   — прогоны агента: детерминированные факты, ответы
--                 блочных LLM-агентов, итоговое summary, заземление.
--
--  Принципы (из hypotheses.md):
--    * S2 — факты считаются кодом из сырых полей, готовая проза отчёта
--      (reputationalRisks[].name) хранится, но не является входом выводов;
--    * S5 — каждое утверждение агента ссылается на field_ref (JSON-path);
--    * S6 — полнота данных первоклассный объект (snapshot_coverage);
--    * метрики риска банка неизменяемы (riskLevel / zskRiskLevel as is).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;

-- ------------------------------- ENUMS -------------------------------

DO $$ BEGIN
    CREATE TYPE core.risk_level      AS ENUM ('LOW','MEDIUM','HIGH','UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE core.zsk_level       AS ENUM ('GREEN','YELLOW','RED','UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE core.rep_polarity    AS ENUM ('POSITIVE','NEGATIVE');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE core.founder_role    AS ENUM ('AUTH_PERSON','COFOUNDER');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    -- 4 рабочих блока из blocks_summary_design.md
    CREATE TYPE audit.block_key      AS ENUM ('identity','reliability','finance','experience');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE audit.block_signal   AS ENUM ('NORM','ATTENTION','RISK','NO_DATA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    -- три группы из эталонной аналитики кейсодателя (S4), без числового скора
    CREATE TYPE audit.verdict_group  AS ENUM ('STOP','ENHANCED_CHECK','CONDITIONALLY_OK','NO_DATA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE audit.run_status     AS ENUM ('PENDING','RUNNING','SUCCEEDED','PARTIAL','FAILED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE audit.grounding      AS ENUM ('GROUNDED','UNVERIFIED','NO_REF');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ================================ RAW ================================

-- Сырой документ снапшота один-в-один. Ничего не теряем: $numberLong,
-- $date и прочий Mongo Extended JSON остаются как есть.
CREATE TABLE IF NOT EXISTS raw.report_documents (
    id              bigserial PRIMARY KEY,
    source_ogrn     text        NOT NULL,               -- _id.ogrn
    source_date     timestamptz NOT NULL,               -- _id.date.$date
    inn             text        NOT NULL,               -- report.baseInfo.inn
    document        jsonb       NOT NULL,               -- весь объект целиком
    document_bytes  integer     NOT NULL,
    source_file     text,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_ogrn, source_date)
);
CREATE INDEX IF NOT EXISTS ix_raw_documents_inn ON raw.report_documents (inn);

-- =============================== CORE ================================

-- Идентичность компании. Живёт дольше отдельного отчёта.
CREATE TABLE IF NOT EXISTS core.companies (
    id            bigserial PRIMARY KEY,
    inn           text NOT NULL UNIQUE CHECK (inn ~ '^[0-9]{10,12}$'),
    ogrn          text,
    kpp           text,
    okpo          text,
    short_name    text,
    full_name     text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_companies_ogrn ON core.companies (ogrn);

-- Версия отчёта на дату. Все дочерние таблицы висят на ней,
-- поэтому история сохраняется, а не перезаписывается.
CREATE TABLE IF NOT EXISTS core.report_snapshots (
    id                       bigserial PRIMARY KEY,
    company_id               bigint NOT NULL REFERENCES core.companies(id) ON DELETE CASCADE,
    raw_document_id          bigint REFERENCES raw.report_documents(id) ON DELETE SET NULL,
    report_date              timestamptz NOT NULL,            -- report.reportDate
    -- baseInfo
    address                  text,
    email                    text,
    website                  text,
    company_size             text,
    registration_date        date,
    years_from_registration  smallint,
    -- статус и оценки банка (неизменяемы, принимаем как константу)
    status                   text,                            -- report.status.status
    status_reason            text,
    status_date              date,
    risk_level               core.risk_level NOT NULL DEFAULT 'UNKNOWN',
    zsk_risk_level           core.zsk_level  NOT NULL DEFAULT 'UNKNOWN',
    -- прочие скаляры
    share_capital            numeric(20,2),                   -- foundersInfo.shareCapital
    branches_count           integer NOT NULL DEFAULT 0,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, report_date)
);
CREATE INDEX IF NOT EXISTS ix_snapshots_company_date ON core.report_snapshots (company_id, report_date DESC);
CREATE INDEX IF NOT EXISTS ix_snapshots_risk        ON core.report_snapshots (risk_level, zsk_risk_level);

-- --- Блок 1. Кто это -------------------------------------------------

CREATE TABLE IF NOT EXISTS core.phones (
    id            bigserial PRIMARY KEY,
    snapshot_id   bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    idx           smallint NOT NULL,
    phone_code    text,
    phone_number  text
);
CREATE INDEX IF NOT EXISTS ix_phones_snapshot ON core.phones (snapshot_id);

CREATE TABLE IF NOT EXISTS core.activity_codes (
    id            bigserial PRIMARY KEY,
    snapshot_id   bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    code          text NOT NULL,                 -- ОКВЭД
    description   text,
    is_main       boolean NOT NULL DEFAULT false,
    idx           smallint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_okved_snapshot ON core.activity_codes (snapshot_id);
CREATE INDEX IF NOT EXISTS ix_okved_code     ON core.activity_codes (code);

CREATE TABLE IF NOT EXISTS core.tax_systems (
    id            bigserial PRIMARY KEY,
    snapshot_id   bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    short_name    text,
    full_name     text
);
CREATE INDEX IF NOT EXISTS ix_tax_snapshot ON core.tax_systems (snapshot_id);

CREATE TABLE IF NOT EXISTS core.branches (
    id            bigserial PRIMARY KEY,
    snapshot_id   bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    name          text,
    address       text
);
CREATE INDEX IF NOT EXISTS ix_branches_snapshot ON core.branches (snapshot_id);

CREATE TABLE IF NOT EXISTS core.founders (
    id             bigserial PRIMARY KEY,
    snapshot_id    bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    role           core.founder_role NOT NULL,
    name           text,
    inn            text,
    position_name  text,        -- только для AUTH_PERSON
    position_date  date,        -- только для AUTH_PERSON
    amount         numeric(20,2),
    share          numeric(9,4),
    date_from      date,
    active         boolean,
    idx            smallint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_founders_snapshot ON core.founders (snapshot_id);
CREATE INDEX IF NOT EXISTS ix_founders_inn      ON core.founders (inn) WHERE inn IS NOT NULL;

CREATE TABLE IF NOT EXISTS core.related_companies (
    id                    bigserial PRIMARY KEY,
    snapshot_id           bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    inn                   text,
    ogrn                  text,
    name                  text,
    registration_date     date,
    auth_person_name      text,
    auth_person_position  text
);
CREATE INDEX IF NOT EXISTS ix_related_snapshot ON core.related_companies (snapshot_id);
CREATE INDEX IF NOT EXISTS ix_related_inn      ON core.related_companies (inn) WHERE inn IS NOT NULL;

CREATE TABLE IF NOT EXISTS core.parent_organizations (
    id                   bigserial PRIMARY KEY,
    related_company_id   bigint NOT NULL REFERENCES core.related_companies(id) ON DELETE CASCADE,
    inn                  text,
    ogrn                 text,
    full_name            text,
    parent_date          date
);
CREATE INDEX IF NOT EXISTS ix_parents_related ON core.parent_organizations (related_company_id);

-- --- Блок 2. Надёжность и правовые риски -----------------------------

-- Готовые метки отчёта. Поле name — это проза источника (H4), она
-- хранится для показа пользователю, но выводы строятся по code.
CREATE TABLE IF NOT EXISTS core.reputational_risks (
    id            bigserial PRIMARY KEY,
    snapshot_id   bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    polarity      core.rep_polarity NOT NULL,
    chapter       text NOT NULL,     -- reestrs, arbitr, execproc, finance, manager, okved, ...
    code          text NOT NULL,     -- fnsBlocking, massAddress, liquidationStatus, ...
    name          text               -- готовая формулировка отчёта, НЕ вход для выводов
);
CREATE INDEX IF NOT EXISTS ix_repriskrs_snapshot ON core.reputational_risks (snapshot_id, polarity);
CREATE INDEX IF NOT EXISTS ix_repriskrs_code     ON core.reputational_risks (code);

-- Справочник кодов меток: тяжесть и признак «жёсткий факт» (H3).
CREATE TABLE IF NOT EXISTS core.risk_code_dictionary (
    code          text PRIMARY KEY,
    chapter       text NOT NULL,
    title_ru      text NOT NULL,
    severity      smallint NOT NULL CHECK (severity BETWEEN 1 AND 3),  -- 3 = стоп-фактор
    is_hard_stop  boolean NOT NULL DEFAULT false,
    block         audit.block_key NOT NULL DEFAULT 'reliability'
);

CREATE TABLE IF NOT EXISTS core.arbitration_cases (
    id                bigserial PRIMARY KEY,
    snapshot_id       bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    year              smallint NOT NULL,
    plaintiff_count   integer NOT NULL DEFAULT 0,
    plaintiff_amount  numeric(20,2) NOT NULL DEFAULT 0,
    defendant_count   integer NOT NULL DEFAULT 0,
    defendant_amount  numeric(20,2) NOT NULL DEFAULT 0,
    UNIQUE (snapshot_id, year)
);

CREATE TABLE IF NOT EXISTS core.arbitration_summary (
    snapshot_id    bigint PRIMARY KEY REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    common_count   integer NOT NULL DEFAULT 0,
    common_amount  numeric(20,2) NOT NULL DEFAULT 0,
    pf_count integer NOT NULL DEFAULT 0, pf_amount numeric(20,2) NOT NULL DEFAULT 0,  -- истец, завершено
    pa_count integer NOT NULL DEFAULT 0, pa_amount numeric(20,2) NOT NULL DEFAULT 0,  -- истец, обжалуется
    pp_count integer NOT NULL DEFAULT 0, pp_amount numeric(20,2) NOT NULL DEFAULT 0,  -- истец, в процессе
    df_count integer NOT NULL DEFAULT 0, df_amount numeric(20,2) NOT NULL DEFAULT 0,  -- ответчик, завершено
    da_count integer NOT NULL DEFAULT 0, da_amount numeric(20,2) NOT NULL DEFAULT 0,  -- ответчик, обжалуется
    dp_count integer NOT NULL DEFAULT 0, dp_amount numeric(20,2) NOT NULL DEFAULT 0   -- ответчик, в процессе
);

CREATE TABLE IF NOT EXISTS core.execution_proceedings (
    id               bigserial PRIMARY KEY,
    snapshot_id      bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    number           text,
    proceeding_date  date,
    amount           numeric(20,2),      -- NULL = сумма не раскрыта в источнике
    amount_known     boolean NOT NULL DEFAULT true,
    active           boolean NOT NULL DEFAULT false,
    idx              integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_execproc_snapshot ON core.execution_proceedings (snapshot_id, active);

CREATE TABLE IF NOT EXISTS core.inspections (
    id                 bigserial PRIMARY KEY,
    snapshot_id        bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    erp_id             text,
    type               text,
    form               text,
    authority_name     text,
    start_date         date,
    end_date           date,
    inspection_status  text
);
CREATE INDEX IF NOT EXISTS ix_inspections_snapshot ON core.inspections (snapshot_id);

-- --- Блок 3. Финансовое состояние ------------------------------------

CREATE TABLE IF NOT EXISTS core.fin_reports (
    id                       bigserial PRIMARY KEY,
    snapshot_id              bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    year                     smallint NOT NULL,
    proceeds                 numeric(20,2),   -- выручка
    profit                   numeric(20,2),   -- прибыль
    total_assets             numeric(20,2),
    current_assets_total     numeric(20,2),
    current_stocks           numeric(20,2),
    current_receivables      numeric(20,2),
    current_bankroll         numeric(20,2),
    uncurrent_assets_total   numeric(20,2),
    uncurrent_fixed_assets   numeric(20,2),
    total_liabilities        numeric(20,2),
    capitals                 numeric(20,2),
    long_term_total          numeric(20,2),
    long_term_others         numeric(20,2),
    short_term_total         numeric(20,2),
    short_term_borrowed      numeric(20,2),
    short_term_payables      numeric(20,2),
    UNIQUE (snapshot_id, year)
);

CREATE TABLE IF NOT EXISTS core.fin_coefficients (
    id              bigserial PRIMARY KEY,
    snapshot_id     bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    year            smallint NOT NULL,
    sustainability  numeric(12,4),
    solvency        numeric(12,4),
    profitability   numeric(12,4),
    UNIQUE (snapshot_id, year)
);

-- --- Блок 4. Опыт и позитивные сигналы -------------------------------

CREATE TABLE IF NOT EXISTS core.licenses (
    id                 bigserial PRIMARY KEY,
    snapshot_id        bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    number             text,
    name               text,
    issuing_authority  text,
    issue_date         date,
    end_date           date,
    status             text      -- ACTIVE / INDEFINITE / ...
);
CREATE INDEX IF NOT EXISTS ix_licenses_snapshot ON core.licenses (snapshot_id);

CREATE TABLE IF NOT EXISTS core.procurements (
    id                    bigserial PRIMARY KEY,
    snapshot_id           bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    procurements_year     smallint,
    federal_law_code      text,          -- ФЗ94 / ФЗ223 / ФЗ44
    tender_winner_cnt     integer NOT NULL DEFAULT 0,
    contract_signed_cnt   integer NOT NULL DEFAULT 0,
    contract_signed_amt   numeric(20,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_procurements_snapshot ON core.procurements (snapshot_id);

-- --- Полнота данных (S6) ---------------------------------------------

-- Паспорт полноты по 9 наблюдаемым блокам данных отчёта.
CREATE TABLE IF NOT EXISTS core.snapshot_coverage (
    snapshot_id         bigint PRIMARY KEY REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    has_founders        boolean NOT NULL DEFAULT false,
    has_related         boolean NOT NULL DEFAULT false,
    has_arbitration     boolean NOT NULL DEFAULT false,
    has_execproc        boolean NOT NULL DEFAULT false,
    has_inspections     boolean NOT NULL DEFAULT false,
    has_fin_reports     boolean NOT NULL DEFAULT false,
    has_coefficients    boolean NOT NULL DEFAULT false,
    has_licenses        boolean NOT NULL DEFAULT false,
    has_procurements    boolean NOT NULL DEFAULT false,
    filled_blocks       smallint NOT NULL DEFAULT 0,   -- 0..9
    computed_at         timestamptz NOT NULL DEFAULT now()
);

-- =============================== AUDIT ===============================

-- Детерминированный слой фактов (S2). Кэш вычислений по снапшоту,
-- ключ — (snapshot_id, block). Пересчитывается версией калькулятора.
CREATE TABLE IF NOT EXISTS audit.snapshot_facts (
    id             bigserial PRIMARY KEY,
    snapshot_id    bigint NOT NULL REFERENCES core.report_snapshots(id) ON DELETE CASCADE,
    block          audit.block_key NOT NULL,
    calculator_ver text NOT NULL,
    facts          jsonb NOT NULL,   -- [{id,label,value,unit,field_ref,source}]
    computed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, block, calculator_ver)
);

-- Один проход проверки по одному ИНН.
CREATE TABLE IF NOT EXISTS audit.analysis_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inn               text NOT NULL,
    company_id        bigint REFERENCES core.companies(id) ON DELETE SET NULL,
    snapshot_id       bigint REFERENCES core.report_snapshots(id) ON DELETE SET NULL,
    status            audit.run_status NOT NULL DEFAULT 'PENDING',
    block_model       text,
    summary_model     text,
    calculator_ver    text,
    llm_mode          text,               -- groq | mock
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    latency_ms        integer,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    error             text
);
CREATE INDEX IF NOT EXISTS ix_runs_inn     ON audit.analysis_runs (inn, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_status  ON audit.analysis_runs (status);

-- Ответ блочного агента. Ровно 4 строки на успешный прогон.
CREATE TABLE IF NOT EXISTS audit.run_blocks (
    id                bigserial PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES audit.analysis_runs(id) ON DELETE CASCADE,
    block             audit.block_key NOT NULL,
    signal            audit.block_signal NOT NULL DEFAULT 'NO_DATA',
    headline          text,
    facts_sentence    text,       -- одна фраза с фактами
    interpretation    text,       -- одна фраза с интерпретацией
    findings          jsonb NOT NULL DEFAULT '[]'::jsonb,
    data_gaps         jsonb NOT NULL DEFAULT '[]'::jsonb,
    cannot_assess     jsonb NOT NULL DEFAULT '[]'::jsonb,
    facts_input       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- что реально ушло в модель
    model             text,
    latency_ms        integer,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    raw_response      jsonb,
    error             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, block)
);

-- Итог Summary-LLM поверх четырёх блочных summary.
CREATE TABLE IF NOT EXISTS audit.run_summaries (
    run_id            uuid PRIMARY KEY REFERENCES audit.analysis_runs(id) ON DELETE CASCADE,
    verdict_group     audit.verdict_group NOT NULL DEFAULT 'NO_DATA',
    headline          text,
    narrative         text,
    key_numbers       jsonb NOT NULL DEFAULT '[]'::jsonb,
    top_risks         jsonb NOT NULL DEFAULT '[]'::jsonb,
    positives         jsonb NOT NULL DEFAULT '[]'::jsonb,
    data_gaps         jsonb NOT NULL DEFAULT '[]'::jsonb,
    questions_to_ask  jsonb NOT NULL DEFAULT '[]'::jsonb,
    model             text,
    latency_ms        integer,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    raw_response      jsonb,
    error             text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Заземление (S5): каждое утверждение агента и его ссылка на поле.
-- Метрика приёмки «доля утверждений со ссылкой > 95 %» считается отсюда.
CREATE TABLE IF NOT EXISTS audit.run_statements (
    id           bigserial PRIMARY KEY,
    run_id       uuid NOT NULL REFERENCES audit.analysis_runs(id) ON DELETE CASCADE,
    block        audit.block_key,          -- NULL = утверждение summary-агента
    statement    text NOT NULL,
    fact_id      text,                     -- id факта из детерминированного слоя
    field_ref    text,                     -- JSON-path в исходной карточке
    fact_value   text,
    grounding    audit.grounding NOT NULL DEFAULT 'NO_REF',
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_statements_run ON audit.run_statements (run_id);

-- Экспертная разметка для критериев приёмки (заполняется вручную).
CREATE TABLE IF NOT EXISTS audit.expert_labels (
    id           bigserial PRIMARY KEY,
    statement_id bigint NOT NULL REFERENCES audit.run_statements(id) ON DELETE CASCADE,
    label        text NOT NULL CHECK (label IN ('CONFIRMED','HONEST_REFUSAL','HALLUCINATED')),
    comment      text,
    author       text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ================================ VIEWS ==============================

-- Последний снапшот по компании — точка входа PoC (1 ИНН = 1 проход).
CREATE OR REPLACE VIEW core.v_latest_snapshots AS
SELECT DISTINCT ON (s.company_id)
       s.*, c.inn, c.ogrn AS company_ogrn, c.short_name, c.full_name
FROM   core.report_snapshots s
JOIN   core.companies c ON c.id = s.company_id
ORDER  BY s.company_id, s.report_date DESC;

-- H3: зелёный светофор при наличии негативных меток.
CREATE OR REPLACE VIEW audit.v_green_with_negatives AS
SELECT c.inn, c.short_name, s.id AS snapshot_id, s.risk_level, s.zsk_risk_level,
       count(*) FILTER (WHERE r.polarity = 'NEGATIVE') AS negative_count,
       array_agg(DISTINCT r.code) FILTER (WHERE r.polarity = 'NEGATIVE') AS negative_codes
FROM   core.report_snapshots s
JOIN   core.companies c ON c.id = s.company_id
LEFT   JOIN core.reputational_risks r ON r.snapshot_id = s.id
WHERE  s.zsk_risk_level = 'GREEN' AND s.risk_level IN ('LOW','UNKNOWN')
GROUP  BY c.inn, c.short_name, s.id, s.risk_level, s.zsk_risk_level
HAVING count(*) FILTER (WHERE r.polarity = 'NEGATIVE') > 0;

-- H4: готовая проза отчёта расходится с фактическим числом кодов ОКВЭД.
-- Позитивный маркер okved/massOkved означает «кодов немного», при этом
-- кодов в карточке 10 и более. Считаем коды подзапросом: join-ить
-- activity_codes и reputational_risks в одном запросе нельзя, счётчик
-- размножится произведением строк.
CREATE OR REPLACE VIEW audit.v_okved_contradictions AS
SELECT c.inn, c.short_name, s.id AS snapshot_id,
       (SELECT count(*) FROM core.activity_codes a WHERE a.snapshot_id = s.id) AS okved_total,
       EXISTS (SELECT 1 FROM core.reputational_risks r
                WHERE r.snapshot_id = s.id AND r.polarity = 'POSITIVE' AND r.code = 'massOkved')
           AS praised_few_okved,
       EXISTS (SELECT 1 FROM core.reputational_risks r
                WHERE r.snapshot_id = s.id AND r.polarity = 'NEGATIVE' AND r.code = 'massOkved')
           AS reported_mass_okved
FROM   core.report_snapshots s
JOIN   core.companies c ON c.id = s.company_id
WHERE  (SELECT count(*) FROM core.activity_codes a WHERE a.snapshot_id = s.id) >= 10
  AND  EXISTS (SELECT 1 FROM core.reputational_risks r
                WHERE r.snapshot_id = s.id AND r.polarity = 'POSITIVE' AND r.code = 'massOkved');

-- Метрика заземления по прогону (критерии приёмки 1 и 2).
CREATE OR REPLACE VIEW audit.v_run_grounding AS
SELECT run_id,
       count(*)                                                        AS statements,
       count(*) FILTER (WHERE grounding = 'GROUNDED')                   AS grounded,
       count(*) FILTER (WHERE grounding = 'UNVERIFIED')                 AS unverified,
       round(100.0 * count(*) FILTER (WHERE grounding = 'GROUNDED')
             / NULLIF(count(*), 0), 1)::float8                          AS grounded_pct
FROM   audit.run_statements
GROUP  BY run_id;
