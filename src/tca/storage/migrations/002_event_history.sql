-- ============================================================================
-- Migration 002 — event history accumulation (v0.2)
--
-- Adds the `history` schema: append-only accumulators deduplicated on natural
-- keys. THIS is what defeats the 90-day Admin Insights retention: run the
-- collector monthly and events accumulate forever (INSERT OR IGNORE — always
-- safe to re-run). Plus meta.event_coverage + the gap-detection view.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS history;

-- Columns mirror the curated TS Events field list in the VDS manifest
-- (identity columns are never requested; numeric user ids are Tableau-internal
-- join keys, not identities).
CREATE TABLE IF NOT EXISTS history.events (
  event_id           BIGINT PRIMARY KEY,   -- natural key from TS Events
  event_date         TIMESTAMP NOT NULL,
  event_name         VARCHAR,
  event_type         VARCHAR,
  item_id            BIGINT,
  item_luid          VARCHAR,
  item_type          VARCHAR,
  item_name          VARCHAR,
  project_name       VARCHAR,
  actor_user_id      BIGINT,               -- numeric join key -> TS Users
  actor_site_role    VARCHAR,
  actor_license_role VARCHAR,
  item_owner_id      BIGINT,
  target_user_id     BIGINT,
  first_seen_run     INTEGER NOT NULL      -- provenance, not part of the key
);

-- Which time ranges each run actually covered (conservative: min/max event
-- date seen). Runs further apart than the retention window leave holes —
-- the analysis must SEE them, hence v_event_gaps below.
CREATE TABLE IF NOT EXISTS meta.event_coverage (
  run_id       INTEGER NOT NULL,
  source       VARCHAR NOT NULL,            -- e.g. 'vds:ts_events'
  window_start TIMESTAMP NOT NULL,
  window_end   TIMESTAMP NOT NULL
);

CREATE OR REPLACE VIEW meta.v_event_gaps AS
  WITH w AS (
    SELECT source, window_start, window_end,
           lag(window_end) OVER (PARTITION BY source ORDER BY window_start) AS prev_end
    FROM meta.event_coverage)
  SELECT source, prev_end AS gap_start, window_start AS gap_end,
         date_diff('day', prev_end, window_start) AS gap_days
  FROM w WHERE prev_end IS NOT NULL AND window_start > prev_end;
