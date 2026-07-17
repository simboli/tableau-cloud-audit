# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

The package-file **schema is additive-only** across versions: tables, columns
and views never disappear or change type (enforced by the schema contract
test). Schema versions are tracked independently in `meta.schema_migrations`.

## [Unreleased]

### Added

- **`metadata` collection module** — the Tableau Metadata API (GraphQL):
  published datasources and workbooks with field-level detail — calculated
  fields **including formulas** (collected deliberately: the duplicate-metric
  raw material), sheet/dashboard field usage, embedded vs published
  datasources, upstream tables and databases (lineage). Two fixed, versioned
  queries in the PII manifest are the complete request surface; they ask for
  **no user identity fields** (owners come from REST, already pseudonymised).
  Cursor pagination with resume: an interrupted run continues from the cursor
  stored in the last landed page. Part of the default module set.
- `tca verify` now probes the Metadata API and warns if unreachable.

## [0.1.0] — 2026-07-16

First public release.

### Added

- **CLI** (`tca`): `init` (setup wizard), `verify` (pre-flight checks incl.
  Admin Insights/VDS access), `collect`, `summary`, `peek`, `resolve`,
  `export`.
- **Collection modules**:
    - `rest_core` — users, groups, group membership;
    - `content` — projects, workbooks, views, datasources, per-item
      connections;
    - `automation` — extract refresh tasks, background job history,
      subscriptions;
    - `permissions` — project rules, project default-permission templates,
      per-item workbook/datasource rules (403/404 on single items, e.g.
      Personal Space content, are skipped and counted, never fatal);
    - `activity` — Admin Insights via the VizQL Data Service: TS Events,
      TS Users, Site Content, Tokens, Job Performance.
- **Privacy by construction** — a mandatory scrub gate on the write path,
  driven by a single reviewable PII manifest, with four mechanisms: stable
  `U-####` pseudonyms for Tableau user objects; redaction of credential
  usernames; query-time column minimization for VDS sources; e-mail →
  pseudonym reverse vault lookup. A blocking safety net rejects any payload
  where an e-mail-shaped string or a known user LUID survives scrubbing.
- **Package file** (one DuckDB file per site, schema v0.4): `meta`
  (runs, migration registry, event coverage with gap detection), `raw`
  (pseudonymised API pages — the source of truth), `state` (typed tables
  rebuilt mechanically from raw on every run, with `v_*_current` views),
  `history` (event log deduplicated on Event Id — outlives Admin Insights'
  90-day retention), `identity` (the vault: the only place real identities
  exist), `clear` (read-time views resolving pseudonyms locally).
- **Optional encryption at rest** (DuckDB native AES via `TCA_DB_KEY`).
- **Checkpoint & resume**: every page commits immediately;
  `tca collect --resume` continues an interrupted run without refetching.
- **Additive-only schema migrations** with registry, auto-heal of older files
  on open, and refusal of files written by a newer collector.
- **Redacted export** (`tca export`): every schema except `identity`,
  verified, unencrypted for inspectability, with SHA-256 sidecar — the only
  artifact meant to leave the collection machine.
- **Docs site** (MkDocs Material): getting-ready guide, CLI reference,
  what-we-collect transparency page (drift-guarded by tests), package file
  schema map, query cookbook (validated against a live site),
  troubleshooting, API coverage tracker.
- **CI** (GitHub Actions): ruff, mypy, full test suite on Python 3.11/3.13;
  the schema contract test gates every merge; docs deploy to GitHub Pages.

[Unreleased]: https://github.com/simboli/tableau-cloud-audit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/simboli/tableau-cloud-audit/releases/tag/v0.1.0
