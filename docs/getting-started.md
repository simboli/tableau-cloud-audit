# Getting started

From zero to your first package file in about ten minutes. Every command here
is copy-paste ready. The first section is preparation — ten minutes there save
an afternoon of 401s, and `tca verify` confirms each step for you.

## Before you begin

| Requirement | Why |
|---|---|
| A **Tableau Cloud** site | The collector targets Cloud (not Tableau Server) |
| A user with the **Site Administrator** role | Only admins see all users, content and permissions; lesser roles produce silently incomplete data |
| **Python ≥ 3.11** on the collection machine | The collector is a Python CLI |
| Outbound HTTPS to `<pod>.online.tableau.com` | The only host the collector ever talks to |

Disk space is a non-issue: even sites with thousands of items produce files in
the tens of MB.

If you are in a jurisdiction where analysing per-user activity needs review by
legal, the DPO or employee representatives, do that *before* the first run —
see [Legal and works-council review](what-we-collect.md#legal-and-works-council-review).

### Create the Personal Access Token

1. Sign in to Tableau Cloud **as the Site Administrator user**.
2. Top-right avatar → **My Account Settings**.
3. Scroll to **Personal Access Tokens** → enter a token name (e.g.
   `tca-collector`) → **Create Token**.
4. **Copy the secret now** — Tableau shows it exactly once.

Worth knowing:

- The token **name** goes in `collector.toml` (`pat_name`); the **secret** only
  ever lives in the `TCA_PAT_SECRET` environment variable.
- PATs expire after **15 days without use** (and at their configured expiry
  date). If collection is monthly, expect to mint a fresh token per run — or
  sign in with it in between.
- The PAT can be **revoked the moment the run ends**: the collector needs no
  standing access.
- If your site enforces MFA, PATs are the supported way to automate — they
  bypass the interactive MFA prompt by design.

### Find your site name and pod

Open your site in the browser and read the URL:

```
https://10ax.online.tableau.com/#/site/simbolidev820124/home
        └─┬─┘                          └───────┬──────┘
         pod                            site (contentUrl)
```

- `site` is the **contentUrl** — the segment after `/site/`. ⚠ It often differs
  from the display name (dashes and spaces are stripped: display
  `simboli-dev-820124` → contentUrl `simbolidev820124`). A wrong value yields a
  401 even with a valid PAT.
- `pod` is the **first label** of the hostname only (`10ax`, `prod-uk-a`,
  `eu-west-1a`) — not the full hostname, not a URL.

### Provision Admin Insights

The time dimension (who opened what, real last-access dates, tokens, job
history) comes from Tableau's **Admin Insights** datasources. They exist only
after an administrator opens the project once:

1. In Tableau Cloud, open the **Admin Insights** project (Explore → Admin
   Insights).
2. Wait for the datasources (TS Events, TS Users, Site Content, …) to appear.
3. Data refreshes on **Tableau's daily schedule** — a token created today shows
   up in the Tokens datasource tomorrow.

Skipping this step is fine: `tca collect` simply skips the activity sources
(with a warning) and collects everything else. Retention is ~90 days (365 with
Advanced Management) — one more reason to **collect monthly**: the package file
accumulates events beyond the retention window.

### Decide on encryption

Set `TCA_DB_KEY` before the first collect and the package file is created
**encrypted at rest** (DuckDB native AES). Two things to accept:

- the same passphrase is needed for every subsequent open — **there is no
  recovery** if it is lost;
- GUI clients need a modern DuckDB driver and an explicit
  `ATTACH … (ENCRYPTION_KEY …)` (see [Troubleshooting](troubleshooting.md)).

Unset, the collector warns and writes a plain file. Recommended: encrypt, and
store the passphrase in your password manager.

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

The wizard asks four questions (site, pod, PAT name, file name) and creates an
empty package file. Nothing secret is written to disk.

## 3. Provide the secrets

Secrets are **environment variables only** — never flags, never files:

```bash
export TCA_PAT_SECRET='<the PAT secret you copied>'
export TCA_DB_KEY='<a passphrase you choose>'   # optional but recommended
```

## 4. Verify

```bash
tca verify
```

This checks everything without collecting anything: config, secret, sign-in,
Admin Insights / VizQL Data Service access, Metadata API, package file. Fix what
it flags (each message says how; see also
[troubleshooting](troubleshooting.md)) until you see **All checks passed**.

A common warning on a fresh site: *Admin Insights datasources not found* — open
the Admin Insights project once as described above, then re-run `tca verify`.

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

This writes `<site>-export.duckdb` + a SHA-256 sidecar: every schema **except**
the identity vault, unencrypted so anyone can inspect exactly what it contains
before it travels. Pseudonyms only — the export is the only artifact meant to
leave this machine.

## Kick-off checklist

- [ ] Site Administrator user available
- [ ] PAT created, secret captured, name noted
- [ ] `site` = contentUrl (from the URL, not the display name)
- [ ] `pod` = bare pod name
- [ ] Admin Insights project opened once (datasources visible)
- [ ] Encryption passphrase chosen and stored safely
- [ ] Legal/works-council review done where applicable
- [ ] `tca verify` → *All checks passed*

## Next steps

- Run `tca collect` **at least monthly**: Admin Insights retains ~90 days of
  events, and each run folds them into a history that outlives retention. The
  [runbook](runbook.md) describes the recurring routine.
- Explore the [query cookbook](cookbook/index.md) for ready-made analysis
  queries.
- Read [what we collect](what-we-collect.md) — the field-level transparency page
  — before sharing anything with works councils or security teams.
