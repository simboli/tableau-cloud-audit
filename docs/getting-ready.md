# Getting ready

Everything to prepare **before** the first `tca collect`. Ten minutes here
save an afternoon of 401s — and `tca verify` will confirm each step.

## What you need

| Requirement | Why |
|---|---|
| A **Tableau Cloud** site | The collector targets Cloud (not Tableau Server) |
| A user with the **Site Administrator** role | Only admins see all users, content and permissions; lesser roles produce silently incomplete data |
| **Python ≥ 3.11** on the collection machine | The collector is a Python CLI |
| Outbound HTTPS to `<pod>.online.tableau.com` | The only host the collector ever talks to |

Disk space is a non-issue: even sites with thousands of items produce files
in the tens of MB.

## 1 · Create the Personal Access Token

1. Sign in to Tableau Cloud **as the Site Administrator user**.
2. Top-right avatar → **My Account Settings**.
3. Scroll to **Personal Access Tokens** → enter a token name (e.g.
   `tca-collector`) → **Create Token**.
4. **Copy the secret now** — Tableau shows it exactly once.

Worth knowing:

- The token **name** goes in `collector.toml` (`pat_name`); the **secret**
  only ever lives in the `TCA_PAT_SECRET` environment variable.
- PATs expire after **15 days without use** (and at their configured expiry
  date). If collection is monthly, expect to mint a fresh token per run — or
  sign in with it in between.
- The PAT can be **revoked the moment the run ends**: the collector needs no
  standing access.
- If your site enforces MFA, PATs are the supported way to automate — they
  bypass the interactive MFA prompt by design.

## 2 · Find your site name and pod

Open your site in the browser and read the URL:

```
https://10ax.online.tableau.com/#/site/simbolidev820124/home
        └─┬─┘                          └───────┬──────┘
         pod                            site (contentUrl)
```

- `site` is the **contentUrl** — the segment after `/site/`. ⚠ It often
  differs from the display name (dashes and spaces are stripped:
  display `simboli-dev-820124` → contentUrl `simbolidev820124`). A wrong
  value yields a 401 even with a valid PAT.
- `pod` is the **first label** of the hostname only (`10ax`, `prod-uk-a`,
  `eu-west-1a`) — not the full hostname, not a URL.

## 3 · Provision Admin Insights (for activity data)

The time dimension (who opened what, real last-access dates, tokens, job
history) comes from Tableau's **Admin Insights** datasources. They exist only
after an administrator opens the project once:

1. In Tableau Cloud, open the **Admin Insights** project (Explore → Admin
   Insights).
2. Wait for the datasources (TS Events, TS Users, Site Content, …) to appear.
3. Data refreshes on **Tableau's daily schedule** — a token created today
   shows up in the Tokens datasource tomorrow.

Skipping this step is fine: `tca collect` simply skips the activity sources
(with a warning) and collects everything else. Retention is ~90 days
(365 with Advanced Management) — one more reason to **collect monthly**: the
package file accumulates events beyond the retention window.

## 4 · Decide on encryption

Set `TCA_DB_KEY` before the first collect and the package file is created
**encrypted at rest** (DuckDB native AES). Two things to accept:

- the same passphrase is needed for every subsequent open — **there is no
  recovery** if it is lost;
- GUI clients need a modern DuckDB driver and an explicit `ATTACH … 
  (ENCRYPTION_KEY …)` (see [Troubleshooting](troubleshooting.md)).

Unset, the collector warns and writes a plain file. Recommended: encrypt.

## 5 · Privacy and legal (read before collecting)

The collector reads **per-user activity metadata**. It pseudonymises
identities at write time and the shareable export contains no personal data
(see [What we collect](what-we-collect.md)) — but in some jurisdictions
(notably **Italy and Germany**, works-council rules) analysing per-user
activity may require prior review by legal, the DPO, or employee
representatives. Involve them *before* the first run, with the
[What we collect](what-we-collect.md) page in hand: it exists for exactly
that conversation.

## 6 · Install and verify

```bash
git clone https://github.com/simboli/tableau-cloud-audit
cd tableau-cloud-audit
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

tca init                              # wizard: site, pod, PAT name, file name
export TCA_PAT_SECRET='<the secret>'
export TCA_DB_KEY='<a passphrase>'    # optional but recommended
tca verify
```

`tca verify` checks every prerequisite on this page — config, secret,
sign-in, Admin Insights/VDS access, package file — and tells you what to fix.
When it prints **All checks passed**, run `tca collect`.

## Kick-off checklist

- [ ] Site Administrator user available
- [ ] PAT created, secret captured, name noted
- [ ] `site` = contentUrl (from the URL, not the display name)
- [ ] `pod` = bare pod name
- [ ] Admin Insights project opened once (datasources visible)
- [ ] Encryption passphrase chosen and stored safely
- [ ] Legal/works-council review done where applicable
- [ ] `tca verify` → *All checks passed*
