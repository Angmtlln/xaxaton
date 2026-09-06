-- Read-only projection: no data rewrite. Apply to existing installations with psql.
-- Only identity/relationship fields cross the DB boundary during cross-check.
CREATE OR REPLACE VIEW core.v_connection_candidates AS
SELECT c.inn, s.id AS snapshot_id, s.report_date,
       jsonb_build_object('report', jsonb_build_object(
           'baseInfo', d.document->'report'->'baseInfo',
           'foundersInfo', d.document->'report'->'foundersInfo',
           'relatedCompanies', d.document->'report'->'relatedCompanies',
           'phones', d.document->'report'->'phones')) AS document
FROM core.v_latest_snapshots s
JOIN core.companies c ON c.id = s.company_id
JOIN raw.report_documents d ON d.id = s.raw_document_id;
