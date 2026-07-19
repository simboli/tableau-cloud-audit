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
| `/tasks/extractRefreshes` | refresh tasks: target, schedule, priority, consecutive failures | — |
| `/jobs` | background job history: type, status, timestamps | — |
| `/subscriptions` | subscription subject, target content, schedule | subscriber `user` → U-#### |
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

**vds:groups** (Groups): Group LUID/Name/Minimum Site Role, licensed-on-sign-in
flag, User LUID → U-#### (the membership edge, cross-checking REST).
Never requested: `User Email`, site fields, aggregate counts.

**vds:permissions** (Permissions): Tableau's own user × item × capability
rows — item LUID/name/type, project names, Capability Type, Permission Value
and description, Has Permission?, User Site Role, User LUID → U-####.
Never requested: `User Email`, `Grantee Name` (mixes user e-mails with group
names), `Grantee LUID`/`Grantee Type` (rule grantees already come from REST,
pseudonymised).

**vds:subscriptions** (Subscriptions): subscription LUID/id/status, data
conditions, attachment flags, target item LUID/type/name, schedule
LUID/name/type, created/last-sent dates, task LUID/type, consecutive failure
count, queue/run durations.
Never requested: `Subscriber Email`, `Subscriber User LUID`, `Created By User
Email`, `Created By User LUID`, `Item Owner Email`, `Subject` (free text —
REST `/subscriptions` already collects it), `Subscriber Group *` fields.

**vds:viz_load_times** (Viz Load Times): Request ID/Time, Duration, status
code and type, Item Luid/Type/Name, Item Repository URL, project and workbook
names.
Never requested: `Item Owner Email`, `Project Owner User Name`, `Workbook
Owner User Name`, `HTTP User Agent` (device fingerprint), `HTTP Request URI`
(may embed personal filter values).

## Metadata API (GraphQL)

Two fixed queries — versioned in the manifest, nothing else is ever asked —
walk the content graph with cursor pagination. Neither query requests any
user identity field: owners are collected (and pseudonymised) via REST only,
so these payloads contain no personal data by construction.

**graphql:datasources** (published datasources): name, project name, extract
and certification flags, the field list with types, and upstream
tables/databases (name, schema, connection type).

**graphql:workbooks** (workbooks): name, project name, sheets and dashboards
with the fields each one uses, embedded datasources (fields and upstream
tables), and references to upstream published datasources.

**Calculated-field formulas are collected** — they are the raw material for
duplicate-metric detection ("four definitions of Net Revenue"). Formulas are
treated as content, same policy as workbook and project names: they are
stored verbatim and included in the export. If your formulas embed something
sensitive, review them before sharing an export — the safety net still blocks
anything e-mail-shaped.

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
