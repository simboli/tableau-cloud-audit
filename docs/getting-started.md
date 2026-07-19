# Getting started

From zero to your first package file in about ten minutes. Every command
here is copy-paste ready; the [getting-ready guide](getting-ready.md) covers
the same ground in depth (PAT creation with screenshots-level detail, legal
notes, Admin Insights provisioning) — come back to it if a step below fails.

## What you need before starting

- **Python 3.11+** on the machine that will run the collector.
- A Tableau Cloud **Site Administrator** account (the collector reads what
  you can read — a limited account collects a limited picture).
- A **Personal Access Token** (PAT): Tableau Cloud → your avatar →
  *My Account Settings* → *Personal Access Tokens* → create one (e.g.
  `tca-collector`) and copy the secret now — it is shown only once.
- Your **site name** and **pod**: with a URL like
  `https://10ax.online.tableau.com/#/site/acmeindustries/home`, the pod is
  `10ax` and the site is `acmeindustries` (the part after `/site/` — it may
  differ from the display name: dashes are often stripped).

## 1. Install

```bash
git clone https://github.com/simboli/tableau-cloud-audit.git
cd tableau-cloud-audit
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # PyPI release planned

tca --version
```

## 2. Create a working directory and initialize

The collector writes `collector.toml` and the package file in the current
directory — pick a dedicated folder you will keep (the package file is your
archive: it accumulates history run after run).

```bash
mkdir ~/tableau-audit && cd ~/tableau-audit
tca init
```

The wizard asks four questions (site, pod, PAT name, file name) and creates
an empty package file. Nothing secret is written to disk.

## 3. Provide the secrets

Secrets are **environment variables only** — never flags, never files:

```bash
export TCA_PAT_SECRET='<the PAT secret you copied>'
export TCA_DB_KEY='<a passphrase you choose>'   # optional but recommended
```

`TCA_DB_KEY` encrypts the package file at rest (it will contain real names
and e-mails in the identity vault). If you skip it, the file is written
unencrypted and the collector says so with a warning. **Choose before the
first collect** — and if you set it, store the passphrase in your password
manager: without it the file cannot be opened.

## 4. Verify

```bash
tca verify
```

This checks everything without collecting anything: config, secret, sign-in,
Admin Insights / VizQL Data Service access, Metadata API, package file. Fix
what it flags (each message says how; see also
[troubleshooting](troubleshooting.md)) until you see **All checks passed**.

A common warning on a fresh site: *Admin Insights datasources not found*.
Open the **Admin Insights** project once in the Tableau Cloud UI as an admin
— Tableau provisions it on first visit — then re-run `tca verify`.

## 5. Collect

```bash
tca collect
```

This runs all six modules (users/groups, content, automation, permissions,
activity, metadata). On a mid-sized site expect minutes, not hours; the
permissions module is the slow part (one call per project and per item —
progress bars keep you company).

Interrupted? Nothing is lost — every page commits immediately:

```bash
tca collect --resume
```

## 6. Look at what you collected

```bash
tca summary               # runs, pages, vault size, event-history window
tca peek users            # real names — resolved locally, never stored clear
tca peek rules            # who can see what
tca resolve U-0001        # one pseudonym → full identity
```

Or open the file with any DuckDB client (see the
[schema map](package-file-schema.md) for the ATTACH incantation and starter
queries).

## 7. Produce the shareable copy

```bash
tca export
```

This writes `<site>-export.duckdb` + a SHA-256 sidecar: every schema
**except** the identity vault, unencrypted so anyone can inspect exactly
what it contains before it travels. Pseudonyms only — the export is the
only artifact meant to leave this machine.

## Next steps

- Run `tca collect` **at least monthly**: Admin Insights retains ~90 days of
  events, and each run folds them into a history that outlives retention.
  The [runbook](runbook.md) describes the recurring routine.
- Explore the [query cookbook](cookbook.md) for ready-made analysis queries.
- Read [what we collect](what-we-collect.md) — the field-level transparency
  page — before sharing anything with works councils or security teams.
