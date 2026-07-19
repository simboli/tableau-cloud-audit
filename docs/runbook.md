# Operations runbook

The recurring procedures for running the collector in production: the
monthly collection routine, recovery from interruptions, secret and key
management, and delivering the export. First-time setup lives in
[getting started](getting-started.md).

## The operating cycle at a glance

```text
┌─ monthly (or more often) ────────────────────────────────┐
│  source secrets → tca verify → tca collect → tca summary │
└──────────────────────────────────────────────────────────┘
        │ when an assessment is due
        ▼
  tca export  →  verify checksum  →  send the export file
```

One package file per site, reused forever. The file is the archive: it
accumulates event and job history across runs and holds the pseudonym
mapping — **never delete it** (pseudonyms would regenerate and break
cross-run comparability).

## Routine collection (monthly or better)

Admin Insights retains roughly **90 days** of events. Each collect folds
the current window into `history.events` / `history.job_runs`, so as long
as runs are less than ~90 days apart the accumulated history is gapless.
Monthly is a comfortable cadence; weekly is fine too.

```bash
cd ~/tableau-audit                 # the directory with collector.toml
source .env                        # secrets — see "Secrets & keys" below
tca verify                         # 30 seconds, catches expired PATs early
tca collect
tca summary                        # sanity check, see below
```

After every run, check three things in `tca summary`:

1. **Last run status is `ok`.** A `partial` run is resumable — see below.
2. **The event-history window grew** to (roughly) today.
3. **No coverage-gaps warning.** If runs drifted more than the retention
   window apart, summary warns and `meta.v_event_gaps` lists the holes —
   the fix is discipline, not a command: collect more often.

Partial-module runs are safe: since schema v0.7, a run that collects only
some modules refreshes only those views — everything else keeps showing the
last run that actually collected it.

## Recovering an interrupted run

Interruptions (Ctrl-C, crash, network drop, expired session) cost nothing:
every page commits the moment it lands.

```bash
tca collect --resume     # continues the last interrupted run
```

Already-collected pages are skipped without an API call. If you start a
fresh `tca collect` instead, leftover interrupted runs are marked `aborted`
and the collector tells you.

The run status in `meta.collection_runs` carries the truth:
`ok` = complete · `partial` = interrupted, resumable · `failed` /
`aborted` = superseded.

## Secrets & keys

Recommended pattern — a `.env` file next to `collector.toml`, owner-readable
only, never committed anywhere:

```bash
cat > .env <<'EOF'
export TCA_PAT_SECRET='<PAT secret>'
export TCA_DB_KEY='<package-file passphrase>'
EOF
chmod 600 .env
```

Then every session starts with `source .env`.

**PAT lifecycle.** Tableau Cloud PATs expire after **15 days of non-use**
(and at most 1 year). A monthly-only cadence will hit the idle expiry:
either run `tca verify` every week or two (any successful sign-in resets the
clock), or expect to re-create the PAT before each run — takes a minute,
update `.env` afterwards. `tca verify` failing with a 401 right after a
long pause is almost always this.

**Encryption key.** `TCA_DB_KEY` is chosen once, at file creation, and must
accompany the file for life — there is no recovery without it, so keep it
in a password manager. Losing the key = losing the archive (you would start
a new file, losing history and pseudonym stability).

## Protecting the package file

- **Back it up** like any archive (encrypted file + key stored separately).
  The file at rest is AES-encrypted when `TCA_DB_KEY` was set.
- **Single writer**: DuckDB allows one writing process. Close SQL clients
  (or connect READ_ONLY) before `tca collect`; two concurrent collects on
  the same file fail with a clear error.
- The file **never leaves the machine** — that is the whole trust model.
  Only the export does.

## Delivering an export

```bash
tca export                          # <database>-export.duckdb + .sha256
shasum -a 256 -c *-export.duckdb.sha256    # verify before sending
```

What the export contains — and provably cannot contain:

- every schema **except** `identity` (the vault stays home), so real
  names/e-mails are structurally absent, not just filtered;
- pseudonyms (`U-####`) everywhere a person is referenced;
- unencrypted on purpose: the client can open it with any DuckDB client and
  inspect exactly what is being shared, before sharing it.

The export is re-verified at creation time (an e-mail-shaped string anywhere
aborts it). Send the `.sha256` alongside the file so the recipient can check
integrity.

## Health checks, quick reference

| Symptom | First move |
|---|---|
| `verify` fails on sign-in (401) | PAT expired (15-day idle limit) → new PAT, update `.env` |
| *Admin Insights datasources not found* | Open the Admin Insights project once as an admin, retry |
| *VDS access denied* warning | Grant the PAT user query access on the Admin Insights datasources |
| Run ended `partial` | `tca collect --resume` |
| Coverage-gaps warning in `summary` | Collect more often; holes are listed in `meta.v_event_gaps` |
| Cryptic lock error | Another process has the file open — close it |

Anything deeper: [troubleshooting](troubleshooting.md) maps every known
failure mode from symptom to fix.
