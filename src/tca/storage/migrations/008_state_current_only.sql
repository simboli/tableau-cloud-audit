-- ============================================================================
-- Migration 008 — state is current-only (v0.8)
--
-- normalize_run now REPLACES each collected state table's whole snapshot every
-- run (current-only) instead of accumulating one snapshot per run_id. So
-- state.* holds exactly one snapshot per table and its size tracks the SITE,
-- not the run count — the fix for unbounded state growth.
--
-- Consequences captured here:
--   * v_*_current no longer needs to pick a run — state IS current. This also
--     removes their dependency on meta.v_endpoint_latest_run (migration 007),
--     which read raw.api_responses; dropping that dependency makes `raw` freely
--     prunable by the retention step without breaking the current views.
--   * clear.* joins keyed on run_id are relaxed to business keys: different
--     state tables may now carry different run_ids (each is the run that last
--     collected it), so run_id is provenance, not a join key. Within a table a
--     snapshot is single-run, so business-key joins never fan out.
--
-- run_id columns stay on the state tables (provenance, still in the contract).
-- ============================================================================

CREATE OR REPLACE VIEW state.v_users_current AS SELECT * FROM state.users;
CREATE OR REPLACE VIEW state.v_groups_current AS SELECT * FROM state.groups;
CREATE OR REPLACE VIEW state.v_content_current AS SELECT * FROM state.content_items;
CREATE OR REPLACE VIEW state.v_permission_rules_current AS SELECT * FROM state.permission_rules;

CREATE OR REPLACE VIEW clear.group_members AS
  SELECT g.name AS group_name,
         m.user_pseudo,
         i.full_name,
         i.email
  FROM state.v_groups_current g
  JOIN state.group_members m ON m.group_luid = g.group_luid
  LEFT JOIN identity.map i ON i.pseudonym = m.user_pseudo;

CREATE OR REPLACE VIEW clear.content_owners AS
  SELECT c.item_type,
         c.name AS item_name,
         p.name AS project_name,
         c.owner_pseudo,
         i.full_name AS owner_full_name,
         i.email AS owner_email,
         c.updated_at
  FROM state.v_content_current c
  LEFT JOIN state.projects p ON p.project_luid = c.project_luid
  LEFT JOIN identity.map i ON i.pseudonym = c.owner_pseudo;

-- v_content_current and v_permission_rules_current no longer reference it.
DROP VIEW IF EXISTS meta.v_endpoint_latest_run;

-- One-time heal for files written before v0.8: collapse any accumulated
-- per-run snapshots to the latest run present in each table, so the file is
-- immediately current-only (not only after the next collect). Idempotent:
-- once a table holds a single run_id these are no-ops (and empty tables too —
-- `run_id <> NULL` deletes nothing).
DELETE FROM state.users               WHERE run_id <> (SELECT max(run_id) FROM state.users);
DELETE FROM state.groups              WHERE run_id <> (SELECT max(run_id) FROM state.groups);
DELETE FROM state.group_members       WHERE run_id <> (SELECT max(run_id) FROM state.group_members);
DELETE FROM state.projects            WHERE run_id <> (SELECT max(run_id) FROM state.projects);
DELETE FROM state.content_items       WHERE run_id <> (SELECT max(run_id) FROM state.content_items);
DELETE FROM state.views               WHERE run_id <> (SELECT max(run_id) FROM state.views);
DELETE FROM state.connections         WHERE run_id <> (SELECT max(run_id) FROM state.connections);
DELETE FROM state.permission_rules    WHERE run_id <> (SELECT max(run_id) FROM state.permission_rules);
DELETE FROM state.user_activity       WHERE run_id <> (SELECT max(run_id) FROM state.user_activity);
DELETE FROM state.content_usage       WHERE run_id <> (SELECT max(run_id) FROM state.content_usage);
DELETE FROM state.tokens              WHERE run_id <> (SELECT max(run_id) FROM state.tokens);
DELETE FROM state.vds_group_members   WHERE run_id <> (SELECT max(run_id) FROM state.vds_group_members);
DELETE FROM state.user_capabilities   WHERE run_id <> (SELECT max(run_id) FROM state.user_capabilities);
DELETE FROM state.subscription_health WHERE run_id <> (SELECT max(run_id) FROM state.subscription_health);
DELETE FROM state.viz_loads           WHERE run_id <> (SELECT max(run_id) FROM state.viz_loads);
DELETE FROM state.datasource_fields   WHERE run_id <> (SELECT max(run_id) FROM state.datasource_fields);
DELETE FROM state.upstream_tables     WHERE run_id <> (SELECT max(run_id) FROM state.upstream_tables);
DELETE FROM state.sheet_fields        WHERE run_id <> (SELECT max(run_id) FROM state.sheet_fields);
DELETE FROM state.workbook_datasources WHERE run_id <> (SELECT max(run_id) FROM state.workbook_datasources);
