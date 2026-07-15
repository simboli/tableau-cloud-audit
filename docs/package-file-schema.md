# Package file — schema map

Orientation guide to the DuckDB package file produced by **tableau-cloud-audit**
(`tca`). Current schema version: **v0.4** (July 2026). One file per Tableau Cloud
site; every table carries `run_id` — full snapshot per run, never deltas.

> Opening the file: it is usually **encrypted**. From a SQL client (DBeaver, duckdb
> CLI) connect to an in-memory DuckDB and run:
>
> ```sql
> ATTACH 'path/to/site.duckdb' AS audit (ENCRYPTION_KEY 'passphrase', READ_ONLY);
> ```
>
> DuckDB is single-writer: close other sessions (or use READ_ONLY) before
> `tca collect` runs. The `main` schema you may see is DuckDB's empty default —
> ignore it.

---

## `meta` — the logbook *(who/when/how collected)*

| Object | Contents |
|---|---|
| `file_info` | exactly 1 row: file identity (site, pod, created at, schema version, encrypted y/n) |
| `collection_runs` | 1 row per `tca collect`: timestamps, status (`ok` / `partial` = resumable / `aborted`), collector & API versions, modules run, notes |
| `schema_migrations` | which schema migrations (001…) were applied, when, by which collector version |
| `event_coverage` | the event time-window each run covered |
| `v_event_gaps` 👁 | **holes** in the event history between runs (empty = no gaps) |
| `v_latest_run` 👁 | id of the latest `ok` run — "the current snapshot" |

## `raw` — the source of truth *(pseudonymised API payloads)*

| Object | Contents |
|---|---|
| `api_responses` | 1 row = 1 API response page; full JSON in `payload`, **already pseudonymised** (identities appear only as `U-####`). Everything is here, but it is inconvenient to query — that is what `state` is for |

## `state` — the convenient tables *(1 row = 1 object, derived from raw)* ⭐

Rebuilt mechanically from `raw` at the end of every collect. Every table has
`run_id`; the `v_*_current` 👁 views pre-filter on the latest `ok` run.
**This is where analysis queries live.**

| Object | Contents |
|---|---|
| `users` | site users: pseudonym, site role, auth setting, last login |
| `groups`, `group_members` | groups and their membership edges |
| `projects` | project tree (`parent_luid`), LockedToProject/ManagedByOwner, owner |
| `content_items` | workbooks + datasources unified: owner, project, created/updated, size, tags, certified flag |
| `views` | workbook views + all-time view counter (windowed usage lives in `history.events`) |
| `connections` | database connections per item: type, server, embedded credentials y/n, credential-user present y/n |
| `permission_rules` | **1 row per grantee × capability**, incl. project default templates (`is_default_template`, `template_for`) — where "All Users" grants hide |
| `v_users_current`, `v_groups_current`, `v_content_current`, `v_permission_rules_current` 👁 | the same, filtered to the latest ok run |

## `history` — the accumulator that outlives retention

| Object | Contents |
|---|---|
| `events` | the Admin Insights event log (TS Events), **deduplicated on `event_id` and cumulative across runs**: run monthly and history grows beyond Tableau's 90-day window. `first_seen_run` records provenance; user references are numeric join keys (→ TS Users), not identities |

## `identity` — the only place personal data exists 🔐

| Object | Contents |
|---|---|
| `map` | the vault: `U-####` → real name, full name, e-mail, Tableau LUID. **Absent from the export by construction** — it never leaves the client machine |

## `clear` — read-time clear views 👁 *(local file only, never in the export)*

Recombine `state` with `identity.map` on the fly — same data as `state`, but with
real names/e-mails instead of pseudonyms. Nothing clear is ever stored; on the
export these cannot exist because the vault is absent. Also reachable via
`tca peek users|members|content|rules`.

| Object | Contents |
|---|---|
| `users` | current users with real name/e-mail |
| `group_members` | group name + member identity |
| `content_owners` | content with owner identity |
| `permission_rules` | rules with grantee resolved (user → e-mail, group → name) |

---

## Starter queries

```sql
-- the current content snapshot, readable
SELECT * FROM audit.clear.content_owners;

-- who can see what (watch for 'All Users' grants!)
SELECT * FROM audit.clear.permission_rules WHERE grantee = 'All Users';

-- what the file contains, run by run
SELECT run_id, started_at, status, modules_run
FROM audit.meta.collection_runs ORDER BY run_id;

-- event history health: window and gaps
SELECT min(event_date), max(event_date), count(*) FROM audit.history.events;
SELECT * FROM audit.meta.v_event_gaps;
```

Rule of thumb: **browse `clear.*`** (or `state.v_*_current`), use `history.events`
for the time dimension, and open `raw` only when you need the original API
response of something.

---

*Compatibility promise: the schema is additive-only — tables and columns listed
here never disappear or change type in future versions (enforced by
[`tests/test_schema_contract.py`](../tests/test_schema_contract.py)). New
columns/tables may appear.*
