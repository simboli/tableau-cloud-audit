# CLI reference

Nine commands, no more. Every command also documents itself: `tca --help`,
`tca <command> --help`.

## At a glance

| Command | What it does | Needs |
|---|---|---|
| `tca init` | Wizard: writes `collector.toml`, creates the (optionally encrypted) package file | — |
| `tca verify` | Pre-flight: config, secret, sign-in, Admin Insights/VDS, Metadata API, package file. Collects nothing | `TCA_PAT_SECRET` |
| `tca collect` | Fetch → pseudonymise → land pages → rebuild typed state. Resumable, page by page | `TCA_PAT_SECRET` (+ `TCA_DB_KEY` if encrypted) |
| `tca summary` | File contents: runs, pages, vault size, event/job history, coverage gaps | `TCA_DB_KEY` if encrypted |
| `tca runs` | The run log: every collection run with status, duration, pages, modules. `--last -q` for scripting | `TCA_DB_KEY` if encrypted |
| `tca diagnostics` | PII-free health & scale report, safe to paste into a GitHub issue. Reads the file only, no network | `TCA_DB_KEY` if encrypted |
| `tca peek VIEW` | Browse the latest snapshot **with real identities** (local only; `users`, `members`, `content`, `rules`) | `TCA_DB_KEY` if encrypted |
| `tca resolve U-####` | One pseudonym → full identity from the local vault | `TCA_DB_KEY` if encrypted |
| `tca export [OUTPUT]` | The shareable copy: everything except the identity vault, verified, + SHA-256 sidecar | `TCA_DB_KEY` if encrypted |

## Global behavior

- **Configuration**: commands read `collector.toml` from the current
  directory; override with `--config/-c PATH`.
- **Secrets are environment variables only** — never flags, never files:

| Variable | Required by | Purpose |
|---|---|---|
| `TCA_PAT_SECRET` | `verify`, `collect` | The PAT secret |
| `TCA_DB_KEY` | any command touching an **encrypted** file | Package-file passphrase; if unset at creation time, the file is written unencrypted (with a warning) |

- **Exit codes**: `0` success · `1` actionable user error (clear message,
  no traceback) · `130` interrupted with Ctrl-C (run is resumable).
- `tca --version` prints the collector version.

---

## `tca init`

Interactive wizard: writes `collector.toml` (site, pod, PAT name, database
file name) and creates the package file — encrypted if `TCA_DB_KEY` is set.

| Option | Effect |
|---|---|
| `--force` | Overwrite an existing `collector.toml` |

Refuses to overwrite without `--force`. Secrets are never written to the
config file.

## `tca verify`

Pre-flight check, no data collected: config loads → `TCA_PAT_SECRET` set →
sign-in works (prints REST API version and site LUID) → Admin Insights
present and VizQL Data Service reachable (warns if not: the activity module
would be skipped) → Metadata API reachable (warns if not: the metadata
module would be skipped) → package file opens.

Run it at kick-off and after any config change.

## `tca collect`

The main event: fetch → pseudonymise → land into the package file, then
rebuild the typed `state` tables. Every page commits immediately — an
interrupted run loses nothing.

| Option | Effect |
|---|---|
| `--modules/-m LIST` | Comma-separated module list. Overrides `[collect] modules` in the config; if neither is set, the full default set runs: `rest_core,content,automation,permissions,activity,metadata` |
| `--resume` | Continue the most recent interrupted run instead of starting a new one |

Modules:

| Module | Collects |
|---|---|
| `rest_core` | users, groups, group membership |
| `content` | projects, workbooks, views, datasources, per-item connections |
| `automation` | extract refresh tasks, background job history, subscriptions |
| `permissions` | project rules, default templates, per-item workbook/datasource rules |
| `activity` | Admin Insights via VDS: TS Events (also accumulated into `history.events`), TS Users, Site Content, Tokens, Job Performance (also accumulated into `history.job_runs`), Groups, Permissions, Subscriptions, Viz Load Times |
| `metadata` | Metadata API (GraphQL): field-level lineage, calculated fields with formulas, sheet-level field usage, upstream tables/databases |

Behavior worth knowing: runs are recorded with a status (`ok` complete ·
`partial` interrupted, resumable · `aborted` superseded by a newer run);
per-item 403/404 (e.g. Personal Space content) are skipped and counted, never
fatal; starting a fresh collect marks leftover interrupted runs as aborted
and says so.

## `tca summary`

What the file contains: site identity, schema version, encryption flag, run
count, response pages, identity-vault size, event- and job-run-history
counts, the event-history window and **coverage gaps** (warns when runs were
further apart than the retention window).

## `tca runs`

The run log: every collection run, newest first, with its status, start
time, duration, landed pages and modules; run notes (e.g. why a run was
aborted) are printed below the table.

| Option | Effect |
|---|---|
| `--limit/-n N` | Show only the most recent N runs (default: all) |
| `--last` | Only the most recent run |
| `--quiet/-q` | Print only each run's status, one per line (machine-readable) |

`tca runs --last -q` prints just the last run's status (`ok`, `partial`,
`failed`, ...) so a scheduled pipeline can branch on it — e.g. run
`tca collect --resume` after a `partial` instead of starting fresh. Fails
with exit code `1` if the package file does not exist yet.

## `tca diagnostics`

A health & scale report that is **PII-free by design**, meant to be pasted
into a bug or scale report. It reads the package file only (no network, no
PAT): versions, run history, per-table row counts and integrity signals —
never names, e-mails, LUIDs or your site identity (site name, pod and PAT
name are omitted). Run notes are scrubbed, and the whole report passes a
final PII self-gate before it is emitted. Without a package file it still
reports the environment.

| Option | Effect |
|---|---|
| `--format/-f FORMAT` | `text`, `markdown` or `json` (default `text`). `markdown` is ready for a GitHub issue |
| `--output/-o PATH` | Write the report to a file instead of stdout |

## `tca peek VIEW`

Browse the latest snapshot **with real identities** — resolved locally by
joining the identity vault; structurally impossible on an export.

| Argument/Option | Effect |
|---|---|
| `VIEW` | One of `users`, `members`, `content`, `rules` |
| `--limit/-n N` | Max rows to display (default 50) |

## `tca resolve PSEUDONYM`

One pseudonym → full identity (name, e-mail, LUID, first/last seen run),
read exclusively from the local vault. Example: `tca resolve U-0042`.

## `tca export [OUTPUT]`

Produce the shareable copy: every schema **except** `identity`, plain
(unencrypted, inspectable) DuckDB, verified — an e-mail-shaped string
anywhere aborts the export — plus a SHA-256 sidecar file.

| Argument/Option | Effect |
|---|---|
| `OUTPUT` | Destination path (default: `<database>-export.duckdb`) |
| `--force` | Overwrite a previous export |

The original package file never leaves your machine; the export is the only
artifact meant to.

---

## One-liners you will actually use

```bash
tca init                                    # first-time setup wizard
source .env && tca verify                   # is everything still working?
tca collect                                 # the monthly run
tca collect --resume                        # pick up an interrupted run
tca collect -m rest_core                    # quick users-only refresh
tca summary                                 # what is in the file?
tca runs --last -q                          # last run's status, for scripts
tca diagnostics -f markdown                 # PII-free report for a GitHub issue
tca peek rules -n 100                       # who can see what (real names)
tca resolve U-0042                          # who is this pseudonym?
tca export && shasum -a 256 -c *.sha256     # shareable copy + integrity check
tca collect -c /path/to/collector.toml      # run against another site's config
```
