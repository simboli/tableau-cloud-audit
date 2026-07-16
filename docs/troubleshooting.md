# Troubleshooting

Symptoms → cause → fix. Every entry below is a failure mode we have actually
seen; error messages are quoted as the tool prints them. First reflex when
something is off: **`tca verify`** — it checks config, secret, sign-in and
Admin Insights access with one command.

## Sign-in and connectivity

### "Signin Error … check the PAT name, the TCA_PAT_SECRET environment variable, and the site name"

Tableau rejected the sign-in (HTTP 401). In order of likelihood:

1. **Wrong site name.** The `site` in `collector.toml` must be the
   **contentUrl** — the part after `/site/` in your browser URL — which often
   differs from the display name. Real example: display name
   `simboli-dev-820124`, contentUrl `simbolidev820124` (**dashes stripped**).
   A wrong site gives a 401 even with a perfectly valid PAT.
2. **PAT name mismatch.** `pat_name` must be the token's *name* exactly as
   shown under *My Account Settings → Personal Access Tokens* — not the
   secret, not your username.
3. **Expired PAT.** Tableau Cloud PATs expire after **15 days without use**
   (and at their configured expiry). Create a new token and re-export
   `TCA_PAT_SECRET`.
4. **Stale environment variable.** `echo ${#TCA_PAT_SECRET}` should print a
   plausible length (~50+). Remember: `export` only lives in the current
   terminal window.

### "Network error … nodename nor servname provided, or not known"

DNS could not resolve the host — almost always a malformed `pod`. It must be
the **bare pod name** (`10ax`, `prod-uk-a`, `eu-west-1a`): the first label of
your Tableau Cloud hostname, nothing more.

### "pod must be the bare pod name (e.g. '10ax' …)"

Same as above, caught earlier: you put a full hostname or URL in `pod`.
Use just the first label.

### "The TCA_PAT_SECRET environment variable is not set"

Export it in the same terminal you run `tca` from:
`export TCA_PAT_SECRET='<secret>'`. It is never read from files by design.

## The package file

### "… is encrypted but no key was provided. Set the TCA_DB_KEY environment variable." / "Wrong encryption key"

The file was created with encryption: every open needs the same passphrase in
`TCA_DB_KEY`. There is no recovery without it — the passphrase you chose at
creation is the only key. (If it is truly lost, the data is gone: delete the
file and re-collect; see the pseudonym warning below.)

### "… is in use by another process. Is another tca run in progress?"

DuckDB allows **one writer at a time**. Close other sessions on the same file
(duckdb CLI, DBeaver, a running `tca collect`). For browsing while keeping
the file available, attach read-only:

```sql
ATTACH 'site.duckdb' AS audit (ENCRYPTION_KEY 'passphrase', READ_ONLY);
```

### "'file.duckdb' belongs to site 'X' … but this run targets 'Y'"

One package file per site, enforced. Point `database` in `collector.toml` to
a different file for the second site.

### "… written by a newer collector than this one … Update tableau-cloud-audit and retry"

The file's schema version is ahead of the installed collector. Update the
collector; never downgrade a file.

### My GUI client (DBeaver, …) shows an empty "memory" database

The connection did not actually open your (encrypted) file — you are looking
at an in-memory session. In a SQL editor run the `ATTACH` above, then refresh
the tree: the file appears as its own catalog. Requires a **DuckDB driver ≥
1.4** (encryption support); update it via the client's driver manager.

### `zsh: command not found: duckdb`

The Python package used by the collector does not ship the CLI shell.
`brew install duckdb`, or just use `tca summary` / `tca peek`.

## Collecting

### The run stopped mid-way (Ctrl-C, crash, network drop)

Nothing is lost: every landed page is already committed.

```bash
tca collect --resume
```

continues the interrupted run, skipping what was collected — including the
per-item API calls. A new `tca collect` (without `--resume`) instead starts a
fresh run and marks the interrupted one `aborted`.

### "⚠ N item(s) refused a permissions query (403/404) and were skipped"

Expected. Some content — typically **Personal Space** workbooks — refuses
permission queries even to site administrators. The items are counted in the
run notes and skipped; everything else proceeds.

### "⚠ Admin Insights datasources not found …"

The Admin Insights project is provisioned the **first time an administrator
opens it** in the browser. Open it once, wait for the datasources to appear,
re-run. Until then the activity module skips those sources (the REST modules
are unaffected).

### Tokens / Job Performance come back empty

Admin Insights extracts refresh **on Tableau's daily schedule** — a token
created today appears tomorrow. An empty extract may also surface as a single
all-null row: same cause.

### "Endpoint '…' is not registered in the PII manifest" / "Safety net: …"

The privacy layer refusing to write, by design (fail loud, never leak). If
you hit this on an unmodified release, a Tableau payload changed shape in a
way the manifest does not cover — please
[open an issue](https://github.com/simboli/tableau-cloud-audit/issues) with
the error message (it contains no personal data).

## Identities and pseudonyms

### After deleting the file, pseudonyms changed

Expected, and the reason the file must be treated as an archive: the
`U-####` mapping lives in the file's identity vault. Delete the file and the
next collect assigns fresh pseudonyms — history in any previously shared
export is no longer comparable. **Don't delete the file**; back it up.

### `tca peek` / `clear.*` views show pseudonyms with empty names

The vault learns identities from `/users` pages. Users that appear only as
content owners or group members (e.g. Tableau system accounts hidden from the
user listing) have a pseudonym but no attributes — normal.
