-- ============================================================================
-- tableau-cloud-audit — package file schema (v0.1)
--
-- One DuckDB file per Tableau Cloud site. Three schemas:
--
--   meta     : bookkeeping — file identity, one row per collection run
--   raw      : the data — one row per API response page, payload as JSON,
--              ALREADY pseudonymised (user identities appear only as U-####)
--   identity : a single table mapping pseudonyms to real identities.
--              THE ONLY PLACE real names/emails/LUIDs may ever exist.
--
-- This script is idempotent (safe to run on every open) and migrations, once
-- they exist, must be additive-only: the file is a long-lived archive.
--
-- Timestamps: TIMESTAMP (naive), always UTC by convention.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS identity;

-- ---------------------------------------------------------------- meta -----

-- Exactly one row: the identity of this package file.
CREATE TABLE IF NOT EXISTS meta.file_info (
  site_luid       VARCHAR NOT NULL,
  site_name       VARCHAR NOT NULL,          -- the contentUrl, e.g. 'acme-industries'
  pod             VARCHAR,                   -- e.g. 'eu-west-1a'
  file_created_at TIMESTAMP NOT NULL,
  schema_version  VARCHAR NOT NULL,
  is_encrypted    BOOLEAN NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS meta.run_seq;

-- One row per `tca collect` execution.
CREATE TABLE IF NOT EXISTS meta.collection_runs (
  run_id            INTEGER PRIMARY KEY DEFAULT nextval('meta.run_seq'),
  started_at        TIMESTAMP NOT NULL,
  finished_at       TIMESTAMP,
  status            VARCHAR NOT NULL DEFAULT 'running',  -- running|ok|partial|failed
  collector_version VARCHAR NOT NULL,
  rest_api_version  VARCHAR,                 -- resolved from /serverinfo at run start
  duckdb_version    VARCHAR NOT NULL,        -- storage-format compatibility record
  modules_run       VARCHAR[],               -- e.g. ['rest_core']
  notes             VARCHAR
);

-- ---------------------------------------------------------------- raw ------

-- One row = one HTTP response page, payload stored verbatim EXCEPT that all
-- user identity fields have been replaced with U-#### pseudonyms before the
-- INSERT (see tca/pseudo/). No typed columns by design: typed state/history
-- tables are a later milestone.
CREATE TABLE IF NOT EXISTS raw.api_responses (
  run_id      INTEGER NOT NULL,
  endpoint    VARCHAR NOT NULL,              -- e.g. '/users', '/groups/{luid}/users'
  entity_luid VARCHAR,                       -- set for per-item calls (e.g. the group); NULL for lists
  page        INTEGER NOT NULL,
  fetched_at  TIMESTAMP NOT NULL,
  payload     JSON NOT NULL
);

-- ---------------------------------------------------------------- identity -

CREATE SEQUENCE IF NOT EXISTS identity.pseudo_seq;

-- The pseudonym vault. Pseudonyms are stable forever: the lookup key is the
-- Tableau user LUID, the table only grows, and re-identification
-- (`tca resolve U-0042`) reads exclusively from here.
CREATE TABLE IF NOT EXISTS identity.map (
  pseudonym             VARCHAR PRIMARY KEY,  -- 'U-0042'
  user_luid             VARCHAR NOT NULL UNIQUE,
  name                  VARCHAR,              -- Tableau username
  full_name             VARCHAR,
  email                 VARCHAR,
  external_auth_user_id VARCHAR,
  first_seen_run        INTEGER NOT NULL,
  last_seen_run         INTEGER NOT NULL
);
