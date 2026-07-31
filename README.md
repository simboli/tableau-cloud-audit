# tableau-cloud-audit

[![CI](https://github.com/simboli/tableau-cloud-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/simboli/tableau-cloud-audit/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/simboli/tableau-cloud-audit/blob/main/LICENSE)
[![Python ≥ 3.11](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](https://github.com/simboli/tableau-cloud-audit/blob/main/pyproject.toml)
[![Docs](https://img.shields.io/badge/docs-github%20pages-teal.svg)](https://simboli.github.io/tableau-cloud-audit/)

**Your entire Tableau Cloud estate in one DuckDB file.**

Tableau Cloud tells you what happened yesterday. It won't tell you who hasn't signed in
since March, which workbooks nobody has opened all year, or who can actually see the HR
dashboard — and Admin Insights forgets everything after 90 days. This collector answers
those questions with SQL, from a file you own.

![tca demo: verify, collect, peek](https://raw.githubusercontent.com/simboli/tableau-cloud-audit/main/docs/assets/demo.gif)

An open-source, client-executed collector that reads a Tableau Cloud site through its
official APIs (REST + Admin Insights via the VizQL Data Service) and lands everything
in a single local DuckDB file — so you can understand optimization opportunities on
**economics** (license waste), **privacy & security** (who can see what), and
**governance** (zombie content, ownerless assets).

> **Status: working alpha.** Collects users, groups, memberships, projects, workbooks,
> views, data sources, connections, permission rules (incl. project default templates)
> and the Admin Insights activity sources (TS Events, TS Users, Site Content, Tokens,
> Job Performance). Interfaces and schema may still change; install from source.

## Design principles

- **You run it, on your machine, with your revocable PAT.** No credentials shared, no
  remote access, no telemetry. When the run is done, revoke the token.
- **Privacy by construction, not by discipline.** User identities are pseudonymised
  (`U-####`) *before* anything is written — enforced by a mandatory scrub gate on the
  write path, not by convention (details below).
- **No hidden logic.** The collector extracts and stores — it computes nothing about
  you. Every endpoint and field it reads is documented in
  [docs/api-coverage.md](https://github.com/simboli/tableau-cloud-audit/blob/main/docs/api-coverage.md),
  and the complete definition of "what counts as personal data" lives in one reviewable
  file: [`src/tca/pseudo/manifest.py`](https://github.com/simboli/tableau-cloud-audit/blob/main/src/tca/pseudo/manifest.py).
- **One file, growing over time.** Run it monthly: events are accumulated and
  deduplicated, so your history outlives Tableau's 90-day Admin Insights retention —
  and coverage gaps between runs are detected, not hidden.

## Quickstart

```bash
pip install -e .                      # from a clone; PyPI release planned

tca init                              # wizard → collector.toml + package file
export TCA_PAT_SECRET='<PAT secret>'  # never stored in files
export TCA_DB_KEY='<passphrase>'      # optional: encrypt the file at rest
tca verify                            # PAT, site, Admin Insights/VDS access
tca collect                           # collect everything (resumable)
```

Then explore:

```bash
tca summary           # what's in the file: runs, row counts, event history, gaps
tca peek users        # browse the latest snapshot WITH real names (local only)
tca resolve U-0042    # one pseudonym → identity (local vault only)
tca export            # the shareable copy: everything EXCEPT the identity vault
```

Interrupted run (crash, Ctrl-C)? Nothing is lost: `tca collect --resume` continues
where it stopped, skipping already-collected pages — API calls included.

## What you can answer

Once the file exists it's just DuckDB — no API, no rate limits, no waiting. The first
pass at license waste, seats paying for nobody:

```sql
SELECT user_pseudo, site_role, last_login_at,
       date_diff('day', last_login_at, current_timestamp::TIMESTAMP) AS days_inactive
FROM state.v_users_current
WHERE last_login_at IS NULL
   OR last_login_at < current_timestamp::TIMESTAMP - INTERVAL 90 DAY
ORDER BY days_inactive DESC NULLS FIRST;
```

```
┌─────────────┬───────────┬─────────────────────┬───────────────┐
│ user_pseudo │ site_role │    last_login_at    │ days_inactive │
├─────────────┼───────────┼─────────────────────┼───────────────┤
│ U-0117      │ Creator   │ NULL                │          NULL │
│ U-0042      │ Creator   │ 2025-09-14 08:21:03 │           320 │
│ U-0288      │ Explorer  │ 2026-01-27 16:44:51 │           185 │
│ U-0031      │ Creator   │ 2026-03-02 09:05:12 │           151 │
└─────────────┴───────────┴─────────────────────┴───────────────┘
```

Swap `state.v_users_current` for `clear.users` and the same query comes back with real
names — that join happens locally, against a vault the shared export doesn't contain.

Six more validated recipes — broad "All Users" grants, content nobody opens, ownerless
assets, embedded credentials — in the
[query cookbook](https://simboli.github.io/tableau-cloud-audit/cookbook/).

## The DuckDB package file

Everything lives in one DuckDB database with **five schemas**:

| Schema | Contents | PII |
|---|---|---|
| **`meta`** | Bookkeeping: file identity, one row per collection run, schema migration registry, event-coverage windows with gap detection | none |
| **`raw`** | The source of truth: one row per API response page, payload stored as JSON, **already pseudonymised** | none — user identities appear only as `U-####` |
| **`state`** | Queryable typed tables (1 row = 1 object: users, groups, projects, content, views, connections, permission rules), rebuilt mechanically from `raw` on every run; `v_*_current` views show the latest snapshot | none |
| **`history`** | Append-only accumulators deduplicated on natural keys (`history.events`): run monthly and the event log outlives the 90-day retention | none |
| **`identity`** | A single table (`identity.map`): the pseudonym ↔ real identity mapping. **The only place real identities exist.** | yes — by design, isolated here |

The schema is versioned: opening a file applies pending additive-only migrations
automatically, and files written by a newer collector are refused rather than mangled.
Tables and columns never disappear across versions — the guarantee is enforced by a
schema contract test in CI. Full table-by-table guide:
[docs/package-file-schema.md](https://github.com/simboli/tableau-cloud-audit/blob/main/docs/package-file-schema.md).

Set `TCA_DB_KEY` to encrypt the whole file at rest (DuckDB native AES encryption). If
unset, the collector warns and writes an unencrypted file.

## How pseudonymisation works

Four mechanisms, all declared in the PII manifest and enforced by a scrub gate that
every payload must pass before it can be written:

1. **User objects → stable pseudonyms.** Tableau user objects (in `/users`, group
   memberships, content owners, permission grantees) have their identity fields
   replaced by a stable `U-####`; the real identity goes into the local vault. Same
   person = same pseudonym, forever, across runs.
2. **Credential redaction.** Database usernames in connections carry no Tableau
   identity to map — they become `[redacted]`, keeping the presence signal.
3. **Query-time minimization.** For Admin Insights sources the collector chooses the
   columns: identity columns it doesn't need (`Actor User Name`, `Owner Email` in
   Site Content, device names, error messages…) are **never even requested**.
4. **E-mail reverse lookup.** Sources that identify owners only by e-mail (Tokens,
   Job Performance) are resolved against the vault: known e-mail → `U-####`, unknown
   → `[redacted]`. An e-mail never survives in the file.

A blocking **safety net** backs all of this: after scrubbing, any e-mail-shaped string
or known user LUID still present anywhere in the payload aborts the write. A gap in
the manifest fails loudly instead of leaking silently.

**And your side of the file stays readable:** `tca peek` (or the `clear.*` SQL views)
re-joins pseudonyms with the vault *at read time*, so you browse your own data with
real names. The export physically lacks the vault, so on the shared copy that
recombination is impossible — not merely forbidden.

## Sharing data with an analyst

`tca export` produces the only artifact meant to leave your machine: a plain,
unencrypted DuckDB copy with every schema **except `identity`**, verified (no
e-mail-shaped strings anywhere) and accompanied by a SHA-256 checksum. Open it with
any DuckDB client to inspect exactly what would be shared, before sharing it —
convenience views included:

![querying the export with DuckDB](https://raw.githubusercontent.com/simboli/tableau-cloud-audit/main/docs/assets/query.gif)

More recipes like this in the
[query cookbook](https://simboli.github.io/tableau-cloud-audit/cookbook/).

## Have a question? Ask an AI

Got a doubt about how this project works, or how to use or extend it? Hand
[`AI_GUIDE.md`](https://github.com/simboli/tableau-cloud-audit/blob/main/AI_GUIDE.md) to Claude (or any capable AI assistant) and then
ask away — it briefs the assistant on the project's purpose, architecture,
privacy model, and conventions so you get grounded answers instead of guesses.

## Requirements

- Python ≥ 3.11
- A Tableau Cloud site and a Personal Access Token of a site administrator
- For activity data: the Admin Insights project provisioned (open it once as an
  admin) — `tca verify` checks this for you

## License

Apache-2.0.

Tableau and Tableau Cloud are trademarks of Salesforce, Inc. This project is an
independent open-source tool and is not affiliated with or endorsed by Salesforce.
