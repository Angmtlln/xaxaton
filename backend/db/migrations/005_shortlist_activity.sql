-- Добавляет идентификатор снимка для поиска по существующим ОКВЭД.
-- Таблицы и исходные данные не меняются.
CREATE OR REPLACE VIEW core.v_company_shortlist AS
WITH fin AS (
  SELECT DISTINCT ON (f.snapshot_id) f.snapshot_id, f.year, f.proceeds, f.profit
  FROM   core.fin_reports f
  ORDER  BY f.snapshot_id, f.year DESC
), hard AS (
  SELECT r.snapshot_id, count(*) AS hard_stops
  FROM   core.reputational_risks r
  JOIN   core.risk_code_dictionary d ON d.code = r.code AND d.is_hard_stop
  WHERE  r.polarity = 'NEGATIVE'
  GROUP  BY r.snapshot_id
), exec AS (
  SELECT snapshot_id, count(*) AS proceedings
  FROM   core.execution_proceedings
  GROUP  BY snapshot_id
), base AS (
  SELECT c.inn, c.short_name, fin.year AS fin_year,
         fin.proceeds, fin.profit,
         CASE WHEN (d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationFinished,dfAmount}' IS NOT NULL AND d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationFinished,dfAmount}' <> 'null'::jsonb)
                   AND (d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationAppealed,daAmount}' IS NOT NULL AND d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationAppealed,daAmount}' <> 'null'::jsonb)
                   AND (d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationPending,dpAmount}' IS NOT NULL AND d.document #> '{report,arbitrationByStatus,defandantArbitration,defandantArbitrationPending,dpAmount}' <> 'null'::jsonb)
              THEN a.df_amount + a.da_amount + a.dp_amount END AS claims_amount,
         CASE WHEN jsonb_typeof(d.document #> '{report,reputationalRisks,negative}') = 'array'
              THEN COALESCE(hard.hard_stops, 0) END AS hard_stops,
         CASE WHEN jsonb_typeof(d.document #> '{report,executionProceedings}') = 'array'
              THEN COALESCE(exec.proceedings, 0) END AS enforcement_count,
         s.risk_level::text AS risk_level, s.zsk_risk_level::text AS zsk_risk_level,
         s.id AS snapshot_id
  FROM   core.v_latest_snapshots s
  JOIN   core.companies c ON c.id = s.company_id
  LEFT   JOIN raw.report_documents d ON d.id = s.raw_document_id
  LEFT   JOIN fin ON fin.snapshot_id = s.id
  LEFT   JOIN core.arbitration_summary a ON a.snapshot_id = s.id
  LEFT   JOIN hard ON hard.snapshot_id = s.id
  LEFT   JOIN exec ON exec.snapshot_id = s.id
)
SELECT * FROM base;
