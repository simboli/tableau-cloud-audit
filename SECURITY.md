# Security

This tool runs with a **site-administrator Personal Access Token** against your
Tableau Cloud site. That demands maximum transparency about what it touches and
how secrets and personal data are handled. This document is the summary; every
claim below is verifiable in code.

## What the collector reads — and what it never reads

It calls only official read endpoints (Tableau REST API and the VizQL Data
Service over the Admin Insights datasources). The complete, always-current
endpoint list is in [docs/api-coverage.md](docs/api-coverage.md).

It never reads:

- **the data inside your dashboards or data sources** (no queries against your
  business data — the only VDS queries target Tableau's own Admin Insights
  telemetry);
- passwords or credentials of any kind (connection credential *usernames* are
  redacted to a presence flag before storage);
- webhook payloads, comment bodies, or anything it does not store.

It performs **no writes** to your site (the only POSTs are sign-in/sign-out and
read-only VDS queries), sends **no telemetry**, and talks to no host other than
your own Tableau Cloud pod.

## Secrets handling

- The PAT secret is read **exclusively** from the `TCA_PAT_SECRET` environment
  variable — never from files, flags, or the package file, and it is excluded
  from `repr()`/logs/error messages (HTTP errors are reported without headers).
- The optional database passphrase (`TCA_DB_KEY`) follows the same rule.
- The PAT is revocable by you the moment the run ends; the session token is
  discarded at sign-out.

## Personal data

Pseudonymisation happens **at write time, inside the collector** — a mandatory
scrub gate on the write path, not a post-processing step:

- Tableau user identities become stable `U-####` pseudonyms; the mapping lives
  in exactly one table (`identity.map`) which `tca export` strips by
  construction, so the shareable copy contains no personal data at all.
- The complete definition of what counts as personal data, per endpoint, lives
  in one reviewable file: [`src/tca/pseudo/manifest.py`](src/tca/pseudo/manifest.py).
- A blocking safety net rejects any payload where an e-mail-shaped string or a
  known user LUID survives scrubbing: a manifest gap fails loudly instead of
  leaking silently.
- The package file supports encryption at rest (DuckDB native AES via
  `TCA_DB_KEY`).

## Where to look when reviewing

| Concern | File |
|---|---|
| What counts as PII, per endpoint | `src/tca/pseudo/manifest.py` |
| Scrub gate + safety net | `src/tca/pseudo/scrubber.py` |
| Auth, retries, secret-free errors | `src/tca/transport/` |
| Storage, encryption, identity vault | `src/tca/storage/writer.py` |
| Redacted export | `PackageStore.export_redacted` + `tca export` |

## Reporting a vulnerability

Please report suspected vulnerabilities privately to
**nicola.simboli@gmail.com** — do not open a public issue for security matters.
You will receive an acknowledgement within a few days. This project is in
alpha: only the latest release is supported with fixes.
