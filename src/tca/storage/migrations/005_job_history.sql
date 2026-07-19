-- ============================================================================
-- Migration 005 — job-run history accumulation (v0.5)
--
-- Same pattern as history.events (002): an append-only accumulator that
-- outlives the Admin Insights retention window, deduplicated on the natural
-- key (INSERT OR IGNORE — always safe to re-run). Coverage windows land in
-- the existing meta.event_coverage (it has a source column), so
-- meta.v_event_gaps reports job-history holes with no extra machinery.
-- ============================================================================

-- Columns mirror the curated Job Performance field list in the VDS manifest
-- (Error Message and subscriber/owner identity fields are never requested;
-- Owner Email arrives already pseudonymised as U-#### / [redacted]).
CREATE TABLE IF NOT EXISTS history.job_runs (
  job_id                       BIGINT PRIMARY KEY,   -- natural key from Job Performance
  job_luid                     VARCHAR,
  job_type                     VARCHAR,
  job_result                   VARCHAR,
  final_job_result             VARCHAR,
  was_manual_run               BOOLEAN,
  item_id                      BIGINT,
  item_luid                    VARCHAR,
  item_type                    VARCHAR,
  item_name                    VARCHAR,
  parent_project_name          VARCHAR,
  schedule_luid                VARCHAR,
  schedule_name                VARCHAR,
  created_at                   TIMESTAMP,
  queued_at                    TIMESTAMP,
  started_at                   TIMESTAMP,
  completed_at                 TIMESTAMP,
  job_duration                 DOUBLE,
  job_queued_duration          DOUBLE,
  job_execution_duration       DOUBLE,
  job_overflow_queued_duration DOUBLE,
  was_overflow_queued          BOOLEAN,
  extract_file_size            DOUBLE,
  subscriber_id                BIGINT,               -- numeric join key, not an identity
  owner_email                  VARCHAR,              -- pseudonym (U-####) or [redacted]
  first_seen_run               INTEGER NOT NULL      -- provenance, not part of the key
);
