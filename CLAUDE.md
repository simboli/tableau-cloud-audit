# tableau-cloud-audit — the open-source Tableau Cloud collector

## What this repo is (and isn't)

This repo is **only the collector** — the open-source, client-executed extractor that
reads a Tableau Cloud site via PAT and writes a local DuckDB **package file**.

It is **not** the analysis engine. Effective-permission resolution, usage tiering,
duplicate clustering, scoring, and findings generation are proprietary and live in a
separate, private repo, developed later. If a change would let a reader learn *how a
site gets evaluated*, it does not belong here.

Full product context (why this exists, the trust-boundary architecture, the analyst-side
DuckDB schema, the report deliverables) lives in `../tca-documentation/` — read that for
background, but note it describes the *whole* product; this repo implements only the
client-side slice of it. Treat everything below as **overriding** that documentation
where they conflict — these are the decisions actually taken for this repo.

## Language convention

Conversation with the maintainer happens in Italian. Everything that ships in the repo —
code, identifiers, comments, docstrings, commit messages, CLI output strings, docs — is
**English only**.

## The ambition

This is a public open-source project. The bar isn't "it works" — it's that someone
stumbling on the repo thinks "wow, this is nice" (polished README, clean CLI UX via
`rich`, clear docs, tests, sensible error messages). Sweat the presentation, not just
the mechanics — this is the free/trust-building half of the product, it's the client's
and the community's only window into how carefully the whole thing is built.

## Architecture decisions for this repo (in the order we reached them)

1. **Collector-only.** No `derived`/analysis logic, ever, in this repo.
2. **MVP storage simplification:** the full typed `state`/`history` schema from
   `tca-documentation/sql/collector_package_schema.sql` (with mechanical logic like
   formula normalisation/hashing, event-class collapsing, path materialisation) is
   **deferred**. For now the collector lands **raw API payloads** into a `raw` schema
   (`raw.api_responses`), plus minimal `meta` bookkeeping (`file_info`,
   `collection_runs`). Typed `state`/`history` tables are a later, distinct milestone —
   don't build them speculatively.
   Validated with the maintainer (2026-07-14):
   - `raw.api_responses`: **1 row = 1 HTTP response page** (not exploded per item);
     columns `run_id, endpoint, entity_luid (nullable, for per-item calls), page,
     fetched_at, payload JSON` — payload stored already scrubbed.
   - `identity.map`: **separate typed columns** (`pseudonym PK, user_luid UNIQUE, name,
     full_name, email, external_auth_user_id, first_seen_run, last_seen_run`), not a
     JSON blob. Pseudonyms stable forever, lookup key = `user_luid`, table only grows.
   - `meta.file_info` (single row: site_luid/name, pod, file_created_at,
     schema_version, is_encrypted) + `meta.collection_runs` (run_id seq PK, timestamps,
     status running|ok|partial|failed, collector/rest_api/duckdb versions,
     modules_run[], notes).
   - Timestamps are naive `TIMESTAMP`, always UTC by convention (not TIMESTAMPTZ:
     fetching TIMESTAMPTZ through the duckdb Python client requires pytz — an extra
     runtime dep we don't want).
   - `init_file_info` locks the file to one site: opening it against a different
     site LUID is a hard error (one package file per site).
3. **Pseudonymisation is NOT deferred**, and it's mandatory, not best-effort. No real
   identity (LUID, email, fullName, externalAuthUserId, ...) may ever reach
   `raw.api_responses` or any other general table. Design:
   - **Single identity table**, inside the *same* package `.duckdb` file (not a
     separate file/companion — this was explicitly decided over the alternative of a
     physically separate, ATTACHed `identity.duckdb`). This one table —
     `identity.map` (or similar; finalize in schema.sql) — is the **only** place real
     names/emails/LUIDs may appear, keyed by a stable pseudonym (`U-####`).
   - Everywhere else in the file, only `user_pseudo` appears — never the raw user LUID,
     name, or email.
   - ~~Known architectural debt~~ **CLOSED 2026-07-15 by `tca export`**: the redacted
     copy (every schema except `identity`, unencrypted, e-mail-regex verified, SHA-256
     sidecar) is the ONLY artifact allowed to cross the client→analyst boundary. The
     original package file still never leaves the client machine.
   - **Mechanism — write-time scrubbing, not export-time filtering.** We considered
     "land everything raw, filter PII only on export" and rejected it: the package file
     is explicitly long-lived (reused run after run, accumulating history across
     months), so PII sitting in it at rest for months is a much bigger liability than a
     redaction step could offset, and it breaks the "client can inspect the file before
     sending" trust story. Instead, pseudonymisation is a **mandatory gate in the write
     path**: nothing reaches `storage/writer.py` without being scrubbed first.
   - **How scrubbing works without becoming "analysis logic":** each REST endpoint
     ships a small **declarative identity-field manifest** (e.g. a dict/YAML mapping
     endpoint → list of JSONPath-like field locations: `/users` →
     `["$.id", "$.name", "$.email", "$.fullName"]`, `/workbooks` →
     `["$.owner.id", "$.owner.name"]`). This is I/O-contract metadata, not business
     logic — it says *where* personal fields live in a given endpoint's shape, it
     doesn't decide anything. The pseudonymiser reads the manifest, scrubs matching
     fields, upserts real↔pseudonym pairs into the `identity` table, and only then
     hands the sanitized payload to the writer.
4. **MVP endpoint scope: identity core only.** First working vertical slice = `/users`,
   `/groups`, `/groups/{id}/users` (group membership). This validates the entire chain
   — auth, pagination, scrubbing, writing — before content inventory (workbooks,
   datasources, permissions, etc.) is added. Don't build beyond this slice without
   discussing scope first.
5. **Testing approach:** a live Tableau Cloud sandbox is available. The maintainer
   provides `TCA_PAT_SECRET` (+ site/pod) as an environment variable in-session when
   we're ready to test; Claude runs the CLI directly via Bash against the real
   sandbox during development. Never ask for the PAT in chat text — env var only.
6. **Naming: CLI command is `tca`**, package name `tableau-cloud-audit` (matches the
   GitHub repo). The docs' `tabhealth` naming is superseded. Trademark disclaimer
   ("independent, not affiliated with Salesforce/Tableau") goes in the README.
7. **The package file is encrypted at rest — when a key is provided.** DuckDB (>=1.4)
   native database encryption (AES, `ENCRYPTION_KEY` on ATTACH) with a user-supplied
   passphrase via the `TCA_DB_KEY` env var (never in collector.toml). If `TCA_DB_KEY`
   is **not set**: print a clear warning ("database will not be encrypted") and
   proceed unencrypted — no interactive prompt, no hard error (maintainer's decision).
   Rationale for encrypting: the in-file `identity` table holds real names/emails; a
   stolen laptop / cloud-synced backup must not expose them. Trade-off accepted: an
   encrypted file is not openable with a bare `duckdb file.duckdb` — inspection goes
   through the CLI (or document the ATTACH+key incantation).
8. **Pseudonymisation scope (MVP): users only.** User LUID/name/email/
   externalAuthUserId → `U-####`. Group names stay in the clear (they encode
   departments — needed for cost-per-BU analysis), as do project/workbook names.
   Optional group pseudonymisation (`G-####`) is possible later for sensitive clients.
9. **Analyst delivery = redacted DuckDB copy, not parquet.** The future "export for
   analyst" step is a copy of the package file with the `identity` schema dropped.
   Parquet export stays in the backlog only as an escape hatch for clients with
   "flat files only" security policies — do not build it for the MVP. (DuckDB
   storage-format version compatibility is handled by discipline: the DuckDB version
   is recorded per run in `meta.collection_runs`.)
10. **Post-scrub safety net (mandatory).** Manifest coverage is the leak surface: user
   LUIDs/names appear not only in `/users` but as `owner.id`/`owner.name` across
   workbooks, datasources, projects, permissions... A missing manifest entry must fail
   loudly, not silently write PII. Before any payload is written, assert the sanitized
   JSON contains no email-pattern matches and no LUID already present in the `identity`
   table; a match is a **blocking error**, never a warning.

## Known limitations (declare, don't hide — goes in SECURITY.md / what-we-collect.md)

- **Free-text fields** (workbook descriptions, project names, comments) can contain
  person names; not mechanically solvable — documented limitation, and one more reason
  the safety-net regex check exists.
- **Pseudonym stability lives in the package file.** `U-####` mapping is stored in the
  in-file `identity` table: delete the file and pseudonyms regenerate, breaking
  cross-run comparability. Document clearly: "the file is your archive, don't delete it".
- **DuckDB is single-writer**: two concurrent `collect` runs on the same file must fail
  with a clear error message, not a cryptic lock error.
- **PAT session token expires (~4h sliding)**: silent re-auth belongs in the transport
  layer from day one, long runs depend on it.

## Stack & versions

- **Python**: developed/tested with whatever is locally installed (3.13 is available
  on this machine) — no reason not to use it, nothing here needs bleeding-edge
  features. Declared package compatibility floor: `requires-python = ">=3.11"` (matches
  the original spec, maximizes adoption in enterprise/IT environments that may lag on
  Python versions). Note: `tomllib` (stdlib TOML parsing) is available from 3.11
  onward, so no extra dependency needed for reading `collector.toml`.
- **httpx** (own client, not `tableauserverclient` — see
  `tca-documentation/Collector_Software_Architecture.md` §1 for why), **duckdb**,
  **typer** (CLI), **pydantic** (config), **rich** (console output). Keep the runtime
  dependency list short — this tool touches an admin PAT and gets security-reviewed.
- Environment: **standard `venv`** (`python3 -m venv .venv`), decided over uv for now;
  migration to uv possible later if contributor workflow demands it.
- License: **Apache-2.0** confirmed ("per ora ok") — add the LICENSE file with the
  first code commit.

## Conventions

| Area | Convention |
|---|---|
| Formatter/linter | **ruff** (format + lint in one tool) |
| Type hints | Always on public function signatures; type-checked in CI (mypy or pyright) |
| Docstrings | Only on public API surface (CLI commands, module entry points) — no comments/docstrings on obvious internal code |
| Tests | **pytest**, one test file per module, mirroring `src/` structure |
| Commit messages | Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, ...) |
| Line length | 100 (via ruff) |
| Imports | Absolute from `tabhealth.*`, no deep relative imports |
| Error handling | Fail loud — no silent `except: pass`; network/auth errors propagate with a clear `rich`-formatted message |
| Secrets | Never log or print the PAT, including in HTTP debug/error output — mask it |
| Naming/comments/docs | English only, always (see Language convention above) |

## Repo layout (target — not all built yet)

```
src/tca/              # python package named after the CLI (`tca`)
  cli.py              # typer app: init, collect, verify, summary, resolve
  config.py           # pydantic settings: collector.toml (tomllib) + TCA_* env vars
  transport/          # NOT named `http/` — avoids shadowing/confusion with stdlib http
    client.py         # httpx wrapper: auth header, retry/backoff, pagination
    auth.py           # PAT sign-in, silent re-auth (~4h sliding token)
  sources/
    rest.py           # REST endpoint wrappers (dumb transport, typed responses)
  modules/
    base.py           # Module protocol: name, requires[], run(ctx)
    rest_core.py       # MVP: users, groups, group membership (expand later)
  pseudo/
    manifest.py        # THE declarative endpoint -> PII-field-paths map, in one
    #                    readable place (a security reviewer must find "what counts
    #                    as PII per endpoint" here, and only here)
    scrubber.py        # manifest-driven scrub gate + identity upserts + safety net
  storage/
    schema.sql         # meta + raw + identity (see architecture decisions above)
    writer.py           # one tx per run
tests/
```

## CLI surface (target)

```
tabhealth init                 # wizard -> collector.toml + empty .duckdb file
tabhealth collect [--modules …]
tabhealth verify               # PAT works? site reachable?
tabhealth summary              # row counts / run info
tabhealth resolve U-0341       # pseudonym -> identity (reads the identity table)
```

PAT only via the `TCA_PAT_SECRET` env var — never in config, never in the file.

## Configuration (`collector.toml`)

Non-secret settings live in `collector.toml` next to the package file. Keys agreed so
far (the `init` wizard proposes sensible defaults):

```toml
site = "acme-industries"
pod = "eu-west"
pat_name = "tabhealth-collector"
database = "acme-industries.duckdb"   # user-chosen; path relative to config file, or absolute
```

The PAT **secret** is never in this file — env var only.

**Env vars** (aligned with the `tca` CLI name; the docs' `TABHEALTH_*` names are superseded):
- `TCA_PAT_SECRET` — the PAT secret (required for `collect`/`verify`)
- `TCA_DB_KEY` — passphrase for DuckDB file encryption (optional; missing → warn + write unencrypted)

**Default file locations**: `collector.toml` and the `.duckdb` file live in the current
working directory (where the user runs `tca init`) — no hidden folders in `$HOME`.

**Package author metadata** (pyproject): Nicola Simboli, nicola.simboli@gmail.com (for now).

## Workflow with the maintainer

- Conversation in Italian; repo content in English (see Language convention).
- **Proactively suggest when to commit and propose the commit message** (Conventional
  Commits format) — the maintainer asked to be prompted rather than having to remember.
- Don't start building features beyond the agreed scope without discussing first.
- **Keep `docs/api-coverage.md` updated**: it tracks every API endpoint we call or plan
  to call, with implementation status. Update it in the same commit that implements
  (or drops) an endpoint.

## Status

MVP complete and **verified live** against the maintainer's DataDev sandbox
(site simbolidev820124, pod 10ax) on 2026-07-15: verify/collect/summary/resolve all
work end-to-end, the package file is encrypted, and a direct query confirmed zero PII
in `raw` (identity fields land as U-#### exactly as designed).

Working conventions: **the maintainer commits personally** — propose the commit
message (Conventional Commits), never run `git commit`. Keep this file and
`docs/api-coverage.md` in sync with reality.

Live-testing note: secrets are sourced from a local `.env` (gitignored, chmod 600)
via `source .env && …` when Claude runs the CLI in-session.

Lessons from the live test (already encoded in code/tests):
- `pod` is the bare pod name only ('10ax'); hostname/URL are rejected with a clear
  error (maintainer's decision).
- The site contentUrl may differ from the display name (dashes stripped:
  'simbolidev820124', not 'simboli-dev-820124') — a wrong site gives Tableau 401001
  on signin even with a valid PAT.
- Tableau signout replies 204 No Content; the client treats empty bodies as `{}`.

Done since: content inventory module (`content`), redacted export (`tca export`).

Next milestones: checkpoint/resume (in progress), permissions module, VDS/Admin
Insights activity module, typed state/history layer.

Backlog (agreed with maintainer):
- **Clear views / `tca peek`**: local-only readable views joining `raw` with
  `identity.map` so the CLIENT sees real names when browsing their own file; computed
  at read time, nothing stored, structurally impossible on the export (no identity
  schema there). Agreed 2026-07-15 as the answer to "can't the client see clear
  data?" without moving PII into raw.
- Progress bar for per-item loops in `collect` (current per-page println is noisy).
- Checkpoint/resume design (decided): **`raw.api_responses` IS the checkpoint** —
  units keyed by (run_id, endpoint, entity_luid, page); resume skips landed units.
  Consequence: per-page commits instead of one transaction per run; run `status`
  carries the truth (ok=complete, partial=interrupted/resumable, failed, aborted).
