# What we collect

Field-level transparency: what the collector reads, what gets pseudonymised or
redacted before anything is written, and what is deliberately never requested.

The enforceable source of truth is the PII manifest
([`src/tca/pseudo/manifest.py`](https://github.com/simboli/tableau-cloud-audit/blob/main/src/tca/pseudo/manifest.py)) —
payloads from endpoints not declared there are refused, and a test keeps this
page in sync with it. Endpoint inventory with statuses:
[api-coverage.md](api-coverage.md).

## How to read this page

- **→ U-####**: a Tableau user identity, replaced by a stable pseudonym; the
  real identity goes only into the local `identity.map` vault (stripped from
  any export).
- **→ [redacted]**: an identifying string with no Tableau user to map (e.g. a
  database credential username): value replaced, presence preserved.
- **never requested**: with the VizQL Data Service we choose the columns — the
  listed ones are simply not part of the query.

## REST API

Full response pages are stored as JSON in `raw.api_responses` — verbatim
EXCEPT for the identity replacements listed here.

| Endpoint | Stored | Identity handling |
|---|---|---|
| `/users` | site role, auth setting, last login, domain, locale… | `id`, `name`, `fullName`, `email`, `externalAuthUserId` → U-#### |
| `/groups` | group name (kept in clear by design: names encode departments), domain, min site role | — |
| `/groups/{luid}/users` | membership edges | member user objects → U-#### |
| `/projects` | name, hierarchy, LockedToProject/ManagedByOwner | `owner` → U-#### |
| `/workbooks` | name, project, dates, size, tags, URLs | `owner` → U-#### |
| `/views` | name, workbook, all-time view count | `owner` → U-#### |
| `/datasources` | name, certified flag, extracts flag, type | `owner` → U-#### |
| `/workbooks/{luid}/connections`, `/datasources/{luid}/connections` | connection type, server address, embed-password flag | credential `userName` → [redacted] |
| `/projects/{luid}/permissions`, `/projects/{luid}/default-permissions/workbooks`, `/projects/{luid}/default-permissions/datasources`, `/workbooks/{luid}/permissions`, `/datasources/{luid}/permissions` | grantee × capability rules, Allow/Deny | user grantees and owners → U-####; group grantees kept in clear |
| `/serverinfo` | REST API version (not stored) | — |

## Admin Insights (VizQL Data Service)

Only the columns listed below are requested — nothing else leaves Tableau.

**vds:ts_events** (TS Events): Event Id/Date/Name/Type, Item Id/LUID/Type/Name,
Project Name, Actor User Id, Actor Site Role, Actor License Role, Item Owner
Id, Target User Id.
*User references are Tableau-internal numeric ids (join keys), not identities.*
Never requested: `Actor User Name`, `Item Owner Email`, historical item fields.

**vds:ts_users** (TS Users): User ID, User LUID → U-####, User Name → U-####,
User Email → U-####, User Friendly Name → U-####, User Site Role, User License
Type, User Creation Date, Last Login Date, Days Since Last Login.

**vds:site_content** (Site Content): Item ID/LUID/Type/Name, project names and
level, created/updated/published/last-accessed dates, size, extract flags,
certified flag, database type, view workbook/type, permission flags, tags.
Never requested: `Owner Email`, `Item Parent Project Owner Email`,
`Description` (free text).

**vds:tokens** (Tokens): GUID, Token Identifier, Token Type, PAT Name,
issued/expires/last-used dates, Database Type, Owner Email → U-#### via vault
lookup (unknown e-mail → [redacted]).
Never requested: `Database User Name`, `Device Name`, `Device ID`.

**vds:job_performance** (Job Performance): job id/luid/type/results, item
id/luid/type/name, project name, schedule, timestamps, durations, extract
size, Subscriber ID (numeric), Owner Email → U-#### via vault lookup.
Never requested: `Error Message`, `Subscriber Email`, `Subscription Subject`,
Bridge fields.

## The safety net

After scrubbing, and again at export time, a blocking check rejects any
payload still containing an e-mail-shaped string or a known user LUID. A gap
in the manifest aborts the write — it never leaks silently.

## Known limitations (stated, not hidden)

- **Free-text names can contain people.** Workbook titles, project names, tags
  and PAT names are kept in clear (they are how admins recognise things); if
  someone names a workbook after a person, that name is stored. Free text that
  *looks like an e-mail* is blocked by the safety net.
- The site name/contentUrl appears in stored URLs — it is site metadata, not a
  user identity.
- Group names are kept in clear by design (they usually encode departments,
  which the analysis needs).
