# CLI cheatsheet

Every command on one page. Full details, options and behavior notes live in
the [CLI reference](cli-reference.md); `tca <command> --help` always works.

## All commands

| Command | What it does | Needs |
|---|---|---|
| `tca init` | Wizard: writes `collector.toml`, creates the (optionally encrypted) package file | — |
| `tca verify` | Pre-flight: config, secret, sign-in, Admin Insights/VDS, Metadata API, package file. Collects nothing | `TCA_PAT_SECRET` |
| `tca collect` | Fetch → pseudonymise → land pages → rebuild typed state. Resumable, page by page | `TCA_PAT_SECRET` (+ `TCA_DB_KEY` if encrypted) |
| `tca summary` | File contents: runs, pages, vault size, event/job history, coverage gaps | `TCA_DB_KEY` if encrypted |
| `tca peek VIEW` | Browse the latest snapshot **with real identities** (local only; `users`, `members`, `content`, `rules`) | `TCA_DB_KEY` if encrypted |
| `tca resolve U-####` | One pseudonym → full identity from the local vault | `TCA_DB_KEY` if encrypted |
| `tca export [OUTPUT]` | The shareable copy: everything except the identity vault, verified, + SHA-256 sidecar | `TCA_DB_KEY` if encrypted |

## Global flags, env vars, exit codes

| | |
|---|---|
| `--config/-c PATH` | Use a `collector.toml` other than `./collector.toml` (any command) |
| `--version/-V` | Print the collector version |
| `TCA_PAT_SECRET` | The PAT secret — environment variable only, never a flag or file |
| `TCA_DB_KEY` | Package-file passphrase (optional; unset ⇒ unencrypted, with a warning) |
| Exit codes | `0` success · `1` actionable user error · `130` Ctrl-C (resumable) |

## `tca collect` options

| Option | Effect |
|---|---|
| `--modules/-m LIST` | Comma-separated subset. Default: `rest_core,content,automation,permissions,activity,metadata` |
| `--resume` | Continue the most recent interrupted run |

## One-liners you will actually use

```bash
tca init                                    # first-time setup wizard
source .env && tca verify                   # is everything still working?
tca collect                                 # the monthly run
tca collect --resume                        # pick up an interrupted run
tca collect -m rest_core                    # quick users-only refresh
tca summary                                 # what is in the file?
tca peek rules -n 100                       # who can see what (real names)
tca resolve U-0042                          # who is this pseudonym?
tca export && shasum -a 256 -c *.sha256     # shareable copy + integrity check
tca collect -c /path/to/collector.toml     # run against another site's config
```
