-- ============================================================================
-- Migration 003 — typed state layer (v0.3)
--
-- Queryable tables (1 row = 1 API object), derived MECHANICALLY from the raw
-- pages of each run by tca/normalize.py: raw stays the source of truth, state
-- is rebuilt per run (delete+insert — idempotent, resume-safe). Full snapshot
-- per run, PK (run_id, luid): older runs are the free time machine; "current"
-- is just the latest ok run (views below).
--
-- Identities: user columns hold U-#### pseudonyms (that is what raw contains).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS state;

CREATE TABLE IF NOT EXISTS state.users (
  run_id        INTEGER NOT NULL,
  user_pseudo   VARCHAR NOT NULL,            -- U-####
  site_role     VARCHAR,
  auth_setting  VARCHAR,
  last_login_at TIMESTAMP,
  PRIMARY KEY (run_id, user_pseudo)
);

CREATE TABLE IF NOT EXISTS state.groups (
  run_id        INTEGER NOT NULL,
  group_luid    VARCHAR NOT NULL,
  name          VARCHAR NOT NULL,
  domain        VARCHAR,
  min_site_role VARCHAR,
  PRIMARY KEY (run_id, group_luid)
);

CREATE TABLE IF NOT EXISTS state.group_members (
  run_id      INTEGER NOT NULL,
  group_luid  VARCHAR NOT NULL,
  user_pseudo VARCHAR NOT NULL,
  PRIMARY KEY (run_id, group_luid, user_pseudo)
);

CREATE TABLE IF NOT EXISTS state.projects (
  run_id              INTEGER NOT NULL,
  project_luid        VARCHAR NOT NULL,
  name                VARCHAR NOT NULL,
  parent_luid         VARCHAR,               -- NULL = top level
  content_permissions VARCHAR,               -- LockedToProject | ManagedByOwner
  owner_pseudo        VARCHAR,
  PRIMARY KEY (run_id, project_luid)
);

-- Unified leaf-content grain: lifecycle logic is type-agnostic by design.
CREATE TABLE IF NOT EXISTS state.content_items (
  run_id       INTEGER NOT NULL,
  item_luid    VARCHAR NOT NULL,
  item_type    VARCHAR NOT NULL,             -- workbook | datasource
  name         VARCHAR NOT NULL,
  project_luid VARCHAR,
  owner_pseudo VARCHAR,
  created_at   TIMESTAMP,
  updated_at   TIMESTAMP,
  size_raw     BIGINT,                       -- unit as returned by REST (MB for workbooks)
  is_certified BOOLEAN,                      -- datasources
  has_extracts BOOLEAN,
  tags         VARCHAR[],
  PRIMARY KEY (run_id, item_luid)
);

CREATE TABLE IF NOT EXISTS state.views (
  run_id              INTEGER NOT NULL,
  view_luid           VARCHAR NOT NULL,
  workbook_luid       VARCHAR,
  name                VARCHAR,
  content_url         VARCHAR,
  total_views_alltime BIGINT,                -- REST usage counter: ALL-TIME only
  PRIMARY KEY (run_id, view_luid)
);

CREATE TABLE IF NOT EXISTS state.connections (
  run_id                   INTEGER NOT NULL,
  item_luid                VARCHAR NOT NULL,  -- the workbook/datasource
  connection_luid          VARCHAR,
  conn_type                VARCHAR,           -- postgres | snowflake | textscan…
  server_address           VARCHAR,
  has_embedded_credentials BOOLEAN,
  cred_user_present        BOOLEAN            -- derived from the redacted userName
);

CREATE SEQUENCE IF NOT EXISTS state.rule_seq;

-- Explicit permission rules as returned (one row per grantee × capability).
-- Effective-permission resolution is analysis and does NOT live in this repo.
CREATE TABLE IF NOT EXISTS state.permission_rules (
  run_id              INTEGER NOT NULL,
  rule_id             BIGINT NOT NULL,        -- surrogate, stable within run
  target_luid         VARCHAR NOT NULL,       -- project or content item
  target_type         VARCHAR NOT NULL,       -- project | workbook | datasource
  is_default_template BOOLEAN NOT NULL,       -- project default-permissions
  template_for        VARCHAR,                -- if default: workbook | datasource
  grantee_type        VARCHAR NOT NULL,       -- user | group
  grantee_id          VARCHAR NOT NULL,       -- U-#### or group LUID
  capability          VARCHAR NOT NULL,       -- Read, Write, …
  mode                VARCHAR NOT NULL,       -- Allow | Deny
  PRIMARY KEY (run_id, rule_id)
);

-- ------------------------------------------------------------ convenience --

CREATE OR REPLACE VIEW meta.v_latest_run AS
  SELECT max(run_id) AS run_id FROM meta.collection_runs WHERE status = 'ok';

CREATE OR REPLACE VIEW state.v_users_current AS
  SELECT u.* FROM state.users u JOIN meta.v_latest_run r USING (run_id);

CREATE OR REPLACE VIEW state.v_groups_current AS
  SELECT g.* FROM state.groups g JOIN meta.v_latest_run r USING (run_id);

CREATE OR REPLACE VIEW state.v_content_current AS
  SELECT c.* FROM state.content_items c JOIN meta.v_latest_run r USING (run_id);

CREATE OR REPLACE VIEW state.v_permission_rules_current AS
  SELECT p.* FROM state.permission_rules p JOIN meta.v_latest_run r USING (run_id);
