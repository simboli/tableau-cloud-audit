# CLI reference

Seven commands, no more. Every command also documents itself: `tca --help`,
`tca <command> --help`.

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
would be skipped) → package file opens.

Run it at kick-off and after any config change.

## `tca collect`

The main event: fetch → pseudonymise → land into the package file, then
rebuild the typed `state` tables. Every page commits immediately — an
interrupted run loses nothing.

| Option | Effect |
|---|---|
| `--modules/-m LIST` | Comma-separated module list. Default: `rest_core,content,permissions,activity` |
| `--resume` | Continue the most recent interrupted run instead of starting a new one |

Modules:

| Module | Collects |
|---|---|
| `rest_core` | users, groups, group membership |
| `content` | projects, workbooks, views, datasources, per-item connections |
| `permissions` | project rules, default templates, per-item workbook/datasource rules |
| `activity` | Admin Insights via VDS: TS Events (also accumulated into `history.events`), TS Users, Site Content, Tokens, Job Performance |

Behavior worth knowing: runs are recorded with a status (`ok` complete ·
`partial` interrupted, resumable · `aborted` superseded by a newer run);
per-item 403/404 (e.g. Personal Space content) are skipped and counted, never
fatal; starting a fresh collect marks leftover interrupted runs as aborted
and says so.

## `tca summary`

What the file contains: site identity, schema version, encryption flag, run
count, response pages, identity-vault size, event-history window and
**coverage gaps** (warns when runs were further apart than the retention
window).

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
