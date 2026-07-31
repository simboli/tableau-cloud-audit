# Query cookbook

Starter recipes for the package file — every query is tested against a real
collected file and runs as-is. They are deliberately *starting points*: each
answers a first-pass question and usually raises the next, more interesting one.

## Setup

Any DuckDB client will do (see [Troubleshooting](../troubleshooting.md) if the
file is encrypted):

```sql
ATTACH 'your-site.duckdb' AS audit (ENCRYPTION_KEY 'passphrase', READ_ONLY);
USE audit;
```

All recipes use the `v_*_current` views (latest successful run) and the `clear.*`
views where real names help — remember those resolve identities **only on your
local file**, never on an export.

## The recipes

| Question | Theme |
|---|---|
| [Who hasn't signed in for 90+ days?](inactive-users.md) | Licence waste |
| [What is granted to "All Users"?](all-users-grants.md) | Permission exposure |
| [Which content shows no access events?](unused-content.md) | Zombie content |
| [Who owns content but isn't in the user listing?](ownerless-content.md) | Ownerless assets |
| [Where do embedded credentials live?](embedded-credentials.md) | Security debt |
| [How much content is certified?](certified-content.md) | Governance pulse |
| [What does activity look like over time?](activity-over-time.md) | Event history |

## Where to go from here

The full table-by-table guide is in
[Package file schema](../package-file-schema.md).

These seven are the map's edge, not the territory. Questions like *"who can
actually see this workbook, through every group and inheritance path?"* or
*"which content is safe to archive?"* need resolution logic that goes well beyond
single queries — the collector deliberately does not compute them
([why](https://github.com/simboli/tableau-cloud-audit/blob/main/CONTRIBUTING.md)).
