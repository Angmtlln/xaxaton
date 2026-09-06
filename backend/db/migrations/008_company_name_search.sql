-- Read-only identity search. No source data changes and no extensions.
CREATE OR REPLACE FUNCTION core.normalize_company_name(value text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT btrim(regexp_replace(
    regexp_replace(
      btrim(regexp_replace(translate(lower(COALESCE(value, '')), 'ё«»"„“”', 'е'), '[[:space:]]+', ' ', 'g')),
      '^(общество с ограниченной ответственностью|публичное акционерное общество|непубличное акционерное общество|открытое акционерное общество|закрытое акционерное общество|акционерное общество|индивидуальный предприниматель|ооо|пао|оао|зао|ао|ип)( |$)', '', 'i'),
    '[[:space:]]+', ' ', 'g'));
$fn$;

CREATE OR REPLACE VIEW core.v_company_name_search AS
SELECT c.inn, COALESCE(NULLIF(c.short_name, ''), c.full_name, c.inn) AS name,
       c.full_name, s.address, s.id AS snapshot_id,
       core.normalize_company_name(c.short_name) AS short_key,
       core.normalize_company_name(c.full_name) AS full_key
FROM core.companies c
JOIN core.v_latest_snapshots s ON s.company_id = c.id;

DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'contractors_mcp_reader') THEN
    GRANT SELECT ON core.v_company_name_search TO contractors_mcp_reader;
    GRANT EXECUTE ON FUNCTION core.normalize_company_name(text) TO contractors_mcp_reader;
  END IF;
END;
$grant$;
