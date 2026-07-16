# Contributing

Thanks for considering a contribution! This project has a small surface but two
iron rules — read those first, the rest is ordinary hygiene.

## The two iron rules

**1. Privacy is structural.** No user identity (name, e-mail, LUID, …) may ever
reach any table except `identity.map`. Every endpoint the collector calls MUST
be declared in the PII manifest (`src/tca/pseudo/manifest.py`) — the scrubber
refuses undeclared payloads, and a blocking safety net catches anything that
slips through. If you add an endpoint, add its manifest entry (and its
`docs/api-coverage.md` row) in the same PR. If in doubt whether a field is
identifying: it is.

**2. The schema is a contract — additive-only.** Analysis tooling downstream
depends on today's tables. Migrations (`src/tca/storage/migrations/`) are
numbered and never edited after release; tables, columns and views never
disappear or change type. `tests/test_schema_contract.py` enforces this — do
not "fix" the test to make a PR green. New tables/columns are welcome: add
them via a new migration AND add them to the contract in the same PR
(a test reminds you).

## Dev setup

```bash
git clone https://github.com/simboli/tableau-cloud-audit
cd tableau-cloud-audit
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # 79+ tests, all offline (mocked HTTP)
ruff check . && ruff format --check . && mypy
```

No Tableau site is needed to develop: the test suite mocks every API. If you
have a [Tableau Developer sandbox](https://www.tableau.com/developer), `tca
verify` + `tca collect` against it is the best end-to-end check.

## Conventions

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `test:`, `chore:` …).
- Formatting/linting via **ruff** (line length 100), types checked by **mypy**
  — CI runs all of it on 3.11 and 3.13.
- Everything in the repo is **English** (code, comments, docs, commits).
- Fail loud: no silent `except: pass`; user-facing errors say what to do next.
- Runtime dependency budget is deliberately tiny (httpx, duckdb, typer,
  pydantic, rich). A new runtime dependency needs a very good reason — this
  tool gets security-reviewed by people about to trust it with an admin PAT.
- Keep docs in the same PR as behavior: `docs/api-coverage.md` for endpoints,
  `docs/package-file-schema.md` + the schema contract for schema changes.

## What does NOT belong here

Analysis logic. The collector extracts, pseudonymises, and stores — it never
*evaluates* a site (no scores, tiers, effective-permission resolution, or
recommendations). The only computation allowed is mechanical normalization.
