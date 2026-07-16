# tableau-cloud-audit

**Your entire Tableau Cloud estate in one DuckDB file.**

An open-source, client-executed collector that reads a Tableau Cloud site
through its official APIs (REST + Admin Insights via the VizQL Data Service)
and lands everything in a single local DuckDB file — so you can understand
optimization opportunities on **economics** (license waste), **privacy &
security** (who can see what), and **governance** (zombie content, ownerless
assets).

!!! note "Status: working alpha"
    Interfaces and schema may still change; install from source. The package
    file schema, however, is already covered by an additive-only compatibility
    contract.

## Why it is safe to run

- **You run it, on your machine, with your revocable PAT.** No credentials
  shared, no telemetry, no writes to your site.
- **Privacy by construction.** User identities are pseudonymised (`U-####`)
  *before* anything is written, enforced by a mandatory scrub gate and a
  blocking safety net. Real identities exist in exactly one table — which the
  shareable export physically lacks.
- **No hidden logic.** The collector extracts and stores; it computes nothing
  about you. Every endpoint and field is documented — see
  [What we collect](what-we-collect.md).

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
tca summary           # runs, row counts, event history, coverage gaps
tca peek users        # browse the latest snapshot WITH real names (local only)
tca resolve U-0042    # one pseudonym → identity (local vault only)
tca export            # the shareable copy: everything EXCEPT the identity vault
```

## Learn more

- [What we collect](what-we-collect.md) — field-level transparency
- [Package file schema](package-file-schema.md) — table-by-table guide for
  querying the file
- [API coverage](api-coverage.md) — every endpoint, with implementation status
- [Security policy](https://github.com/simboli/tableau-cloud-audit/blob/main/SECURITY.md)
  · [Contributing](https://github.com/simboli/tableau-cloud-audit/blob/main/CONTRIBUTING.md)

---

*Tableau and Tableau Cloud are trademarks of Salesforce, Inc. This project is
an independent open-source tool and is not affiliated with or endorsed by
Salesforce.*
