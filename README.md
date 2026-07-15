# tableau-cloud-audit

**Your entire Tableau Cloud estate in one DuckDB file.**

An open-source, client-executed collector that reads a Tableau Cloud site through its
official APIs (REST, Admin Insights, Metadata API) and lands everything in a single
local DuckDB file — so you can understand optimization opportunities on **economics**
(license waste), **privacy & security** (who can see what), and **governance**
(zombie content, ownerless assets, duplicated metrics).

> **Status: early development.** The architecture is settled (see below), the first
> vertical slice (users, groups, group membership) is being built. Not usable yet.

## Design principles

- **You run it, on your machine, with your revocable PAT.** No credentials shared, no
  remote access, no telemetry. When the run is done, revoke the token.
- **Privacy by construction.** User identities are pseudonymised (`U-####`) *before*
  anything is written. Real names/emails exist in exactly one table, and the file can
  be encrypted at rest.
- **No hidden logic.** The collector extracts and stores — it computes nothing about
  you. Every field it reads is documented in [docs/api-coverage.md](docs/api-coverage.md).
- **One file, growing over time.** Run it monthly and the file accumulates history
  beyond Tableau's 90-day Admin Insights retention window.

## The DuckDB package file

Everything the collector produces lives in one DuckDB database with **five schemas**:

| Schema | Contents | PII |
|---|---|---|
| **`meta`** | Bookkeeping: file identity (`meta.file_info`), one row per collection run (`meta.collection_runs`), schema migration registry, event-coverage windows with gap detection | none |
| **`raw`** | The source of truth: one row per API response page (`raw.api_responses`), payload stored as JSON, **already pseudonymised** | none — user identities appear only as `U-####` |
| **`state`** | Queryable typed tables (1 row = 1 object: users, groups, projects, content, views, connections, permission rules), rebuilt mechanically from `raw` on every run; `v_*_current` views show the latest snapshot | none |
| **`history`** | Append-only accumulators deduplicated on natural keys (`history.events`): run the collector monthly and the event log outlives Tableau's 90-day retention | none |
| **`identity`** | A single table (`identity.map`): the pseudonym ↔ real identity mapping (`U-0042` → name, email, LUID). **The only place real identities exist.** | yes — by design, isolated here |

Because the `identity` schema is the only PII boundary, sharing a redacted copy of the
file (identity schema dropped) shares zero personal data. The file itself never needs
to leave your machine until you decide it does.

Set `TCA_DB_KEY` to encrypt the whole file at rest (DuckDB native AES encryption). If
unset, the collector warns and writes an unencrypted file.

## Planned CLI

```
tca init          # wizard → collector.toml + empty package file
tca collect       # run the collection
tca verify        # check PAT, site reachability, prerequisites
tca summary       # what's in the file: runs, row counts, coverage
tca resolve U-42  # pseudonym → identity (reads the local identity table)
```

Secrets are environment variables only, never files:
`TCA_PAT_SECRET` (your PAT secret), `TCA_DB_KEY` (optional encryption passphrase).

## Requirements

- Python ≥ 3.11
- A Tableau Cloud site and a Personal Access Token with site-admin visibility

## License

Apache-2.0.

Tableau and Tableau Cloud are trademarks of Salesforce, Inc. This project is an
independent open-source tool and is not affiliated with or endorsed by Salesforce.
