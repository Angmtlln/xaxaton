-- Only a new role and explicit read grants. No source/audit data is modified.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'contractors_mcp_reader') THEN
        CREATE ROLE contractors_mcp_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;
GRANT USAGE ON SCHEMA core, raw TO contractors_mcp_reader;
GRANT SELECT ON core.companies, core.report_snapshots, core.snapshot_coverage,
    core.reputational_risks, core.activity_codes, core.v_latest_snapshots,
    core.v_company_shortlist, core.v_connection_candidates, raw.report_documents
    TO contractors_mcp_reader;
