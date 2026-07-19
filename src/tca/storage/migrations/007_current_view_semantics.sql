-- ============================================================================
-- Migration 007 — per-endpoint "current" semantics (v0.7)
--
-- Fixes the partial-run wart: v_*_current used to filter on the single
-- latest ok run, so a run that collected a SUBSET of modules (e.g. only
-- rest_core) emptied the views of everything it did not collect —
-- clear.permission_rules would show nothing after a users-only refresh.
--
-- New semantics: each v_*_current points to the latest ok run that actually
-- COLLECTED that data. "Collected" is read from raw.api_responses (an empty
-- listing still lands one page, so genuinely-empty data stays empty — no
-- stale ghosts). Views may therefore combine different runs, but each one
-- always shows the freshest snapshot available.
--
-- meta.v_latest_run keeps its original meaning ("latest ok run id").
-- ============================================================================

CREATE OR REPLACE VIEW meta.v_endpoint_latest_run AS
  SELECT r.endpoint, max(r.run_id) AS run_id
  FROM raw.api_responses r
  JOIN meta.collection_runs c ON c.run_id = r.run_id AND c.status = 'ok'
  GROUP BY r.endpoint;

CREATE OR REPLACE VIEW state.v_users_current AS
  SELECT u.* FROM state.users u
  WHERE u.run_id =
    (SELECT run_id FROM meta.v_endpoint_latest_run WHERE endpoint = '/users');

CREATE OR REPLACE VIEW state.v_groups_current AS
  SELECT g.* FROM state.groups g
  WHERE g.run_id =
    (SELECT run_id FROM meta.v_endpoint_latest_run WHERE endpoint = '/groups');

-- content_items is fed by two listings; every module that lands one lands
-- both, so max() over the pair is safe.
CREATE OR REPLACE VIEW state.v_content_current AS
  SELECT c.* FROM state.content_items c
  WHERE c.run_id =
    (SELECT max(run_id) FROM meta.v_endpoint_latest_run
     WHERE endpoint IN ('/workbooks', '/datasources'));

CREATE OR REPLACE VIEW state.v_permission_rules_current AS
  SELECT p.* FROM state.permission_rules p
  WHERE p.run_id =
    (SELECT max(run_id) FROM meta.v_endpoint_latest_run
     WHERE endpoint IN ('/projects/{luid}/permissions',
                        '/projects/{luid}/default-permissions/workbooks',
                        '/projects/{luid}/default-permissions/datasources',
                        '/workbooks/{luid}/permissions',
                        '/datasources/{luid}/permissions'));
