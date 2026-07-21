<!--
  This file is written FOR AN AI ASSISTANT.
  If you are a human: hand this file to Claude (or any capable LLM) and then
  ask your question about tableau-cloud-audit. It gives the assistant enough
  grounding to answer accurately instead of guessing.
  If you are an AI assistant: read this whole file before answering. It is
  the ground truth about this project's purpose, boundaries, architecture,
  and the invariants you must never violate in any suggestion you make.
-->

# AI assistant guide to `tableau-cloud-audit`

You are helping someone understand, use, or extend **tableau-cloud-audit**
(CLI name: `tca`). This document is your briefing. Read it fully, then answer
the user's actual question. When the repository is available to you, prefer
reading the real code over reciting this file — this is orientation, the code
is truth. When it is not available, this file is your best grounding; say so
if you are asked about something it does not cover, rather than inventing it.

Answer in the user's language even though this guide is in English.

---

## 1. What this project is (in one paragraph)

`tableau-cloud-audit` is an **open-source, client-executed collector** for
Tableau Cloud. An administrator runs it with a Personal Access Token (PAT);
it reads their Tableau Cloud site through the REST API, the VizQL Data Service
(Admin Insights), and the Metadata API (GraphQL), and writes everything into
**one local DuckDB file** — pseudonymised by construction, optionally
encrypted at rest. That file is the deliverable: a complete, queryable
snapshot of a Tableau Cloud estate (users, groups, content, permissions,
activity history, lineage) that outlives Tableau's own ~90-day retention.

Tagline the project uses: *"your entire Tableau Cloud estate in one DuckDB
file."*

## 2. What this project is NOT (this matters — do not cross it)

This repository is **only the collector**. It extracts and stores; it does
**not analyze, score, rank, or evaluate** anything.

The analysis engine — effective-permission resolution, usage tiering,
zombie/dormancy scoring, duplicate-metric clustering, findings generation, the
report deliverables — is **proprietary and lives in a separate private
repository**. It is the commercial half of the product; this collector is the
free, trust-building half and the funnel to it.

**The moat rule, which governs every suggestion you make:** if a change would
teach a reader *how a Tableau site gets evaluated* — any logic that turns raw
facts into a judgment (tiers, scores, "this is a problem", effective
permissions, risk levels) — **it does not belong in this repo.** Mechanical
transformations (renaming a field, parsing a timestamp, flattening a list,
deduplicating on a natural key) are fine and welcome. Interpretation is not.

If a user asks you to add analysis/scoring/tiering here, do not just do it —
explain that it belongs in the private engine and offer the mechanical,
in-scope alternative instead. The query cookbook in the docs is allowed to go
*teaser-deep* (show a stale-content query) but never *moat-deep* (never
compute effective permissions or a dormancy score).

## 3. The privacy model (the other hard invariant)

Privacy is **structural, not best-effort**. Internalize this; it constrains
almost every code suggestion.

- **Pseudonymisation is a mandatory gate on the write path.** Nothing reaches
  storage without passing through `Scrubber.scrub()`. Real user identities
  (LUID, username, full name, e-mail, external auth id) are replaced with a
  stable pseudonym `U-####` *before* the payload is written.
- **The single reviewable PII manifest** (`src/tca/pseudo/manifest.py`) is the
  privacy contract. It declares, per endpoint, *where* personal fields live.
  It is I/O-contract metadata, not business logic. Every endpoint the
  collector calls **must** be registered there — an unregistered endpoint's
  payload is refused, never written.
- **The identity vault** (`identity.map` table) is the ONLY place real
  identities exist, keyed by pseudonym. It lives inside the same DuckDB file.
- **The safety net**: after scrubbing, a blocking check rejects any payload
  where an e-mail-shaped string or a known user LUID survived. A gap in the
  manifest fails loudly — it never leaks silently.
- **Three minimization mechanisms** beyond pseudonymisation: credential
  usernames are redacted to `[redacted]`; VDS and GraphQL sources request only
  a curated column/field list (identity columns are never even fetched);
  e-mail-only sources are reverse-looked-up to `U-####` (unknown → `[redacted]`).
- **The export boundary**: `tca export` produces the ONLY file allowed to
  leave the client machine. It is a copy with the `identity` schema **dropped**
  — so real identities are structurally absent, not merely filtered — plus a
  re-run of the safety net and a SHA-256 sidecar. The original file never
  leaves the machine.

When you suggest adding a new data source, the FIRST thing to check is: does
it carry identity, and is it in the manifest? Never propose writing a payload
that bypasses the scrubber.

## 4. Architecture — the shape of the code

Repository layout (Python package `tca`, installed as CLI `tca`):

```
src/tca/
  cli.py              # Typer app: init, verify, collect, summary, peek, resolve, export
  config.py           # pydantic config: collector.toml + TCA_* env vars
  normalize.py        # mechanical raw → typed `state` tables (runs after each collect)
  transport/          # HTTP layer (NOT named http/ to avoid stdlib clash)
    client.py         #   httpx wrapper: auth header, retry/backoff, pagination, silent re-auth
    auth.py           #   PAT sign-in
  sources/            # dumb endpoint wrappers, typed responses (no logic)
    rest.py  vds.py  metadata.py
  modules/            # one extraction domain each; fetch → scrub → land
    base.py           #   Module protocol + RunContext + ModuleStats
    rest_core.py content.py automation.py permissions.py activity.py metadata.py
  pseudo/
    manifest.py       #   THE PII manifest (privacy contract) — REST, VDS, GraphQL sections
    scrubber.py       #   the mandatory scrub gate + safety net + identity upserts
  storage/
    writer.py         #   PackageStore: the DuckDB connection, migrations, history, export
    migrations/       #   numbered, additive-only SQL (001…007); released files are FROZEN
tests/                # pytest, one file per module; ~98 tests
docs/                 # PUBLIC docs only (MkDocs Material site)
```

**The data flow of one `tca collect`:**

1. Sign in (PAT → session token + site LUID).
2. For each selected module, in dependency order: fetch pages from a source,
   pass each through `Scrubber.scrub()`, and `land()` it into
   `raw.api_responses` (one row = one HTTP response page, already scrubbed).
   Every page commits immediately.
3. History accumulation: TS Events → `history.events`, Job Performance →
   `history.job_runs` (deduplicated on a natural key, cumulative across runs —
   this is what outlives Tableau's retention window).
4. `normalize.py` rebuilds the typed `state.*` tables from the raw pages of
   this run (delete + insert; raw stays the source of truth).

**The package-file schema (current version v0.7)** has these DuckDB schemas:

- `meta` — logbook: `file_info`, `collection_runs`, `schema_migrations`,
  `event_coverage`, and views (`v_latest_run`, `v_endpoint_latest_run`,
  `v_event_gaps`).
- `raw` — `api_responses`: the source of truth, scrubbed JSON payloads.
- `state` — convenient typed tables, one row per object, rebuilt each run,
  with `v_*_current` views (each following the latest run that actually
  collected that data — see §7 "known subtleties").
- `history` — `events`, `job_runs`: append-only accumulators.
- `identity` — `map`: the vault (the only real identities; absent from exports).
- `clear` — read-time views re-joining `state` with `identity.map` so the
  local user sees real names (`tca peek` reads these; impossible on an export).

## 5. The CLI surface (7 commands)

Secrets are environment variables ONLY, never flags or files:
`TCA_PAT_SECRET` (the PAT), `TCA_DB_KEY` (optional encryption passphrase).

| Command | Purpose |
|---|---|
| `tca init` | Wizard → `collector.toml` + empty package file |
| `tca verify` | Pre-flight: config, secret, sign-in, Admin Insights/VDS, Metadata API, file. Collects nothing |
| `tca collect` | The main run: fetch → pseudonymise → land → normalize. `--modules/-m`, `--resume` |
| `tca summary` | What the file holds: runs, pages, vault size, history window, coverage gaps |
| `tca peek VIEW` | Browse the latest snapshot WITH real identities (local only): `users`, `members`, `content`, `rules` |
| `tca resolve U-####` | One pseudonym → full identity, from the local vault |
| `tca export [OUT]` | The shareable copy: everything except `identity`, verified, + SHA-256 |

Modules for `collect` (default set = all six):
`rest_core` (users, groups, membership), `content` (projects, workbooks,
views, datasources, connections), `automation` (refresh tasks, jobs,
subscriptions), `permissions` (project + default-template + per-item rules),
`activity` (Admin Insights via VDS: TS Events, TS Users, Site Content, Tokens,
Job Performance, Groups, Permissions, Subscriptions, Viz Load Times),
`metadata` (Metadata API GraphQL: fields, calculated-field formulas, lineage,
sheet field usage).

Exit codes: `0` success · `1` actionable user error (clear message, no
traceback) · `130` Ctrl-C (the run is resumable).

## 6. Conventions and stack (respect these in any code you write)

- **Language**: conversation with the maintainer happens in Italian;
  **everything that ships in the repo is English only** — code, identifiers,
  comments, docstrings, commit messages, CLI output, docs.
- **Stack**: Python ≥3.11 (developed on 3.13). Runtime deps deliberately short
  — `httpx`, `duckdb` (≥1.4, for native encryption), `typer`, `pydantic`,
  `rich`. Own HTTP client, NOT `tableauserverclient`. Standard `venv`.
  License **Apache-2.0**.
- **Tooling**: `ruff` (format + lint, line length 100), `mypy` (strict; types
  on all public signatures), `pytest` (one test file per module). CI runs
  ruff + mypy + the full suite on Python 3.11 and 3.13; **the schema contract
  test gates every merge**.
- **Docstrings** only on the public API surface (CLI commands, module entry
  points). No comments on obvious internal code. A comment states a constraint
  the code can't show — never narrates what the next line does.
- **Error handling**: fail loud. No silent `except: pass`. Network/auth errors
  propagate with a clear `rich`-formatted message. The PAT is never logged or
  printed, including in error output.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:` …).
  **The maintainer commits personally** — an AI should propose the commit
  message, not run `git commit`, unless explicitly asked.
- **Schema migrations are additive-only and released files are FROZEN.** Never
  edit `001`…`007`; a new need gets a new numbered file. Tables/columns/views
  never disappear or change type — the analyst's engine depends on this
  guarantee, enforced by `tests/test_schema_contract.py`. When the schema
  grows, update the golden snapshot in that test in the same change.
- **Docs discipline**: `docs/api-coverage.md` (endpoint status) and
  `docs/package-file-schema.md` / `docs/what-we-collect.md` are kept in sync
  with the code in the same change that alters an endpoint or the schema;
  drift is caught by `tests/test_docs.py`. Only PUBLIC docs live in `docs/` —
  no business/IP material.

## 7. Known subtleties (so you don't "rediscover" or break them)

- **The package file is long-lived and reused run after run.** It IS the
  archive: deleting it regenerates pseudonyms and breaks cross-run
  comparability. History accumulates in it.
- **`raw.api_responses` is the resume checkpoint.** A unit is
  `(run_id, endpoint, entity_luid, page)`; resume skips already-landed units.
  History accumulation reads *back from raw*, so a crash between landing and
  accumulating heals on re-run.
- **`v_*_current` semantics (v0.7)**: each `state.v_*_current` view follows the
  latest ok run that *actually collected that data* (via
  `meta.v_endpoint_latest_run`), NOT the single latest run. This is so a
  partial run (e.g. `-m rest_core`) refreshes only users and doesn't empty the
  permissions/content views. An empty listing still lands a page, so
  genuinely-empty data stays empty (no stale ghosts).
- **`EXPORT_SAFE_VIEWS`** in `writer.py` recreates identity-free convenience
  views inside the export and must stay in sync with migrations 002/003/007.
  The `clear.*` views must NEVER be added there (they touch identity).
- **Encryption is opt-in but recommended**: if `TCA_DB_KEY` is unset the file
  is written unencrypted with a clear warning (maintainer's decision — no hard
  error, no interactive prompt). An encrypted file can't be opened with a bare
  `duckdb file.duckdb`; use the CLI or the documented ATTACH+key incantation.
- **Live-testing detail**: `pod` is the bare pod name (`10ax`), not a URL; the
  site `contentUrl` may differ from the display name (dashes stripped). Both
  are documented failure modes.
- **Tests are hermetic**: HTTP is mocked with `pytest-httpx`; no test touches a
  real Tableau site. When you add a source, add a test in the same style.

## 8. Project status and roadmap (as of mid-2026)

The collector is **feature-complete for its MVP scope and verified live**
against a real Tableau Cloud sandbox. CI is green; the docs site is published.
Schema is at v0.7. ~98 tests pass.

What is deliberately deferred or out of scope (do not "helpfully" build these
without discussing scope first): the proprietary analysis engine (§2); typed
state for VDS sources beyond what exists; several backlog REST endpoints
(flows, virtual connections, data alerts, webhooks, site settings — see
`docs/api-coverage.md` for the tracker with statuses); a Parquet export escape
hatch; optional group pseudonymisation (`G-####`).

Known honest gaps that are NOT code problems (useful context if the user asks
"what would make this successful"): it has only been run against one small
site (scale behaviour on a 10k-user estate is unproven); the free tool lacks a
one-command "wow" artifact (a teaser `tca report` is the highest-leverage idea
on the table); distribution/adoption and the first paid assessment are the
real business risks, not the code; Windows packaging (the typical Tableau
admin persona) is still via `pip`/`uv` rather than a signed installer.

## 9. How to be genuinely useful here

- **Ground your answers in the actual code when you can see it.** File paths in
  this guide are stable pointers; open them.
- **Honor the two invariants above all**: the moat rule (§2) and the privacy
  model (§3). If a request would violate either, say so plainly and offer the
  in-scope alternative — that is more helpful than a compliant-but-wrong patch.
- **Match the house style** (§6): English in the repo, short deps, fail loud,
  additive-only schema, propose commits rather than making them.
- **When unsure, say so.** This project values honesty over confident guessing;
  the maintainer explicitly prefers "I don't know, here's how to find out" to a
  fabricated answer. If this guide and the code disagree, the code wins — and
  point out the drift so the docs can be fixed.
- **Keep public/private separation sacred**: never suggest committing
  business logic, analysis heuristics, or IP material to this public repo.

---

*This guide describes the repository's intent and invariants; the code is the
final authority. If you spot a contradiction between this file and the code,
that is a bug in this file — mention it.*
