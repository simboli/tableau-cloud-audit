-- ============================================================================
-- Migration 006 — typed state for VDS and Metadata API pages (v0.6)
--
-- Same contract as 003: state rows are DERIVED from raw pages, rebuilt per
-- run (delete + insert), strictly mechanical — renaming, timestamp parsing,
-- flattening. Raw stays the source of truth.
--
-- Identity columns arrive ALREADY pseudonymised from raw (the scrubber runs
-- on the write path): *_pseudo columns hold U-#### (or [redacted]), never a
-- real name, e-mail or user LUID.
-- ============================================================================

-- vds:ts_users — real activity beyond the REST lastLogin field
CREATE TABLE IF NOT EXISTS state.user_activity (
  run_id                INTEGER NOT NULL,
  user_id               BIGINT,             -- numeric join key (TS Events actor)
  user_pseudo           VARCHAR,
  site_role             VARCHAR,
  license_type          VARCHAR,
  user_created_at       TIMESTAMP,
  last_login_at         TIMESTAMP,
  days_since_last_login BIGINT
);

-- vds:site_content — Last Accessed At, the zombie-detection backbone
CREATE TABLE IF NOT EXISTS state.content_usage (
  run_id                         INTEGER NOT NULL,
  item_id                        BIGINT,
  item_luid                      VARCHAR,
  item_type                      VARCHAR,
  name                           VARCHAR,
  parent_project_name            VARCHAR,
  top_project_name               VARCHAR,
  project_level                  BIGINT,
  created_at                     TIMESTAMP,
  updated_at                     TIMESTAMP,
  first_published_at             TIMESTAMP,
  last_published_at              TIMESTAMP,
  last_accessed_at               TIMESTAMP,
  size_bytes                     BIGINT,
  is_data_extract                BOOLEAN,
  has_refresh_scheduled          BOOLEAN,
  is_certified                   BOOLEAN,
  database_type                  VARCHAR,
  view_workbook_id               BIGINT,
  view_type                      VARCHAR,
  controlled_permissions_enabled BOOLEAN,
  controlling_project_luid       VARCHAR,
  tags                           VARCHAR
);

-- vds:tokens — PAT hygiene
CREATE TABLE IF NOT EXISTS state.tokens (
  run_id           INTEGER NOT NULL,
  guid             VARCHAR,
  token_identifier VARCHAR,
  token_type       VARCHAR,
  pat_name         VARCHAR,
  issued_at        TIMESTAMP,
  expires_at       TIMESTAMP,
  last_used_at     TIMESTAMP,
  last_updated_at  TIMESTAMP,
  database_type    VARCHAR,
  owner_pseudo     VARCHAR
);

-- vds:groups — membership cross-check vs the REST edges
CREATE TABLE IF NOT EXISTS state.vds_group_members (
  run_id               INTEGER NOT NULL,
  group_luid           VARCHAR,
  group_name           VARCHAR,
  min_site_role        VARCHAR,
  licensed_on_sign_in  BOOLEAN,
  user_pseudo          VARCHAR
);

-- vds:permissions — Tableau's own user × item × capability flattening
CREATE TABLE IF NOT EXISTS state.user_capabilities (
  run_id                    INTEGER NOT NULL,
  item_luid                 VARCHAR,
  item_type                 VARCHAR,
  item_name                 VARCHAR,
  parent_project_name       VARCHAR,
  top_project_name          VARCHAR,
  controlling_project_name  VARCHAR,
  capability                VARCHAR,
  permission_value          BIGINT,
  permission_description    VARCHAR,
  has_permission            BOOLEAN,
  user_pseudo               VARCHAR,
  user_site_role            VARCHAR
);

-- vds:subscriptions — delivery health
CREATE TABLE IF NOT EXISTS state.subscription_health (
  run_id                    INTEGER NOT NULL,
  subscription_luid         VARCHAR,
  subscription_id           BIGINT,
  status                    VARCHAR,
  data_conditions           VARCHAR,
  has_image                 BOOLEAN,
  has_pdf                   BOOLEAN,
  extract_refresh_triggered BOOLEAN,
  item_luid                 VARCHAR,
  item_type                 VARCHAR,
  item_name                 VARCHAR,
  schedule_luid             VARCHAR,
  schedule_name             VARCHAR,
  schedule_type             VARCHAR,
  created_at                TIMESTAMP,
  last_sent_at              TIMESTAMP,
  task_luid                 VARCHAR,
  task_type                 VARCHAR,
  consecutive_failures      BIGINT,
  queued_seconds            DOUBLE,
  run_seconds               DOUBLE
);

-- vds:viz_load_times — request-level load durations (windowed by retention)
CREATE TABLE IF NOT EXISTS state.viz_loads (
  run_id           INTEGER NOT NULL,
  request_id       VARCHAR,
  request_time     TIMESTAMP,
  duration_seconds DOUBLE,
  status_code      VARCHAR,
  status_type      VARCHAR,
  item_luid        VARCHAR,
  item_type        VARCHAR,
  item_name        VARCHAR,
  repository_url   VARCHAR,
  project_name     VARCHAR,
  workbook_name    VARCHAR
);

-- graphql:* — every field of every datasource (formulas included: content,
-- not personal data — maintainer's decision, 2026-07-17)
CREATE TABLE IF NOT EXISTS state.datasource_fields (
  run_id             INTEGER NOT NULL,
  workbook_luid      VARCHAR,             -- NULL for published datasources
  datasource_node_id VARCHAR,             -- Metadata API node id (join key)
  datasource_luid    VARCHAR,             -- NULL for embedded datasources
  datasource_name    VARCHAR,
  is_embedded        BOOLEAN,
  field_node_id      VARCHAR,
  name               VARCHAR,
  field_type         VARCHAR,             -- GraphQL __typename
  role               VARCHAR,
  data_type          VARCHAR,
  is_hidden          BOOLEAN,
  formula            VARCHAR              -- calculated fields only
);

-- graphql:* — lineage: the tables/databases each datasource reads
CREATE TABLE IF NOT EXISTS state.upstream_tables (
  run_id             INTEGER NOT NULL,
  datasource_node_id VARCHAR,
  table_node_id      VARCHAR,
  name               VARCHAR,
  table_schema       VARCHAR,
  full_name          VARCHAR,
  is_embedded        BOOLEAN,
  database_name      VARCHAR,
  database_type      VARCHAR
);

-- graphql:workbooks — which fields each sheet actually uses
CREATE TABLE IF NOT EXISTS state.sheet_fields (
  run_id        INTEGER NOT NULL,
  workbook_luid VARCHAR,
  sheet_node_id VARCHAR,
  sheet_name    VARCHAR,
  field_node_id VARCHAR,
  field_name    VARCHAR,
  kind          VARCHAR                   -- 'worksheet' | 'datasource'
);

-- graphql:workbooks — embedded vs published datasource usage per workbook
CREATE TABLE IF NOT EXISTS state.workbook_datasources (
  run_id             INTEGER NOT NULL,
  workbook_luid      VARCHAR,
  datasource_node_id VARCHAR,
  datasource_luid    VARCHAR,             -- NULL for embedded datasources
  datasource_name    VARCHAR,
  is_embedded        BOOLEAN
);
