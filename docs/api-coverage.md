# API coverage

Tracks every Tableau Cloud API endpoint this collector calls or plans to call.
**Keep this file updated whenever an endpoint is implemented, added, or dropped.**

Status legend:

| Status | Meaning |
|---|---|
| ✅ implemented | Called by the collector today, covered by tests |
| 🚧 in progress | Being built right now |
| 🔜 next | Committed scope for the current milestone |
| 📋 planned | In the roadmap, not yet scheduled |
| 💤 backlog | Known/considered, no commitment |

All REST paths are relative to `https://{pod}.online.tableau.com`; `{v}` is the API
version resolved at runtime from `/serverinfo`, `{site}` is the site LUID returned by
sign-in. Endpoints marked **PII** return user identity fields and must have an entry
in the pseudonymisation manifest (`pseudo/manifest.py`) before they can be called.

## Auth & infrastructure

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/3.4/serverinfo` | GET | Resolve REST API version dynamically (unauthenticated) | — | ✅ implemented |
| `/api/{v}/auth/signin` | POST | PAT sign-in → session token + site LUID; silent re-auth on 401 | — | ✅ implemented |
| `/api/{v}/auth/signout` | POST | Clean session termination at end of run | — | ✅ implemented |

## REST — identity core (MVP milestone)

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/users?fields=_all_` | GET (paginated) | All users: site role, last login, auth setting, … | **yes** (name, fullName, email, externalAuthUserId) | ✅ implemented (`rest_core` module) |
| `/api/{v}/sites/{site}/groups` | GET (paginated) | All groups (names stay in the clear by design) | no | ✅ implemented (`rest_core` module) |
| `/api/{v}/sites/{site}/groups/{group}/users` | GET (paginated, per group) | Group membership edges | **yes** (user objects) | ✅ implemented (`rest_core` module) |

## REST — content inventory

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/projects` | GET (paginated) | Project tree, LockedToProject/ManagedByOwner, owners | **yes** (owner → U-####) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/workbooks?fields=_all_` | GET (paginated) | Workbooks: owner, project, size, updatedAt, tags | **yes** (owner → U-####) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/views?includeUsageStatistics=true` | GET (paginated) | Views + all-time view counts | **yes** (owner → U-####) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/datasources?fields=_all_` | GET (paginated) | Data sources: isCertified, hasExtracts, owner | **yes** (owner → U-####) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/workbooks/{id}/connections` | GET (per item) | Connection types, servers, embedded credentials | **yes** (credential userName → `[redacted]`, presence kept) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/datasources/{id}/connections` | GET (per item) | Same, for data sources | **yes** (same redaction) | ✅ implemented (`content` module) |
| `/api/{v}/sites/{site}/flows` | GET (paginated) | Prep flows inventory | **yes** (owner) | 📋 planned |
| `/api/{v}/sites/{site}/virtualConnections` | GET (paginated) | Virtual connections (REST is the only inventory) | **yes** (owner) | 💤 backlog |

## REST — permissions

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/projects/{id}/permissions` | GET (per project) | Explicit project-level rules | **yes** (user grantees → U-####; group grantees in clear) | ✅ implemented (`permissions` module) |
| `/api/{v}/sites/{site}/projects/{id}/default-permissions/workbooks` | GET (per project) | Default templates children inherit — where "All Users" hides | **yes** (same) | ✅ implemented (`permissions` module) |
| `/api/{v}/sites/{site}/projects/{id}/default-permissions/datasources` | GET (per project) | Same, datasource template | **yes** (same) | ✅ implemented (`permissions` module) |
| `/api/{v}/sites/{site}/workbooks/{id}/permissions` | GET (per item — expensive!) | Content-level rules; 403/404 (Personal Space) skipped + counted | **yes** (same) | ✅ implemented (`permissions` module) |
| `/api/{v}/sites/{site}/datasources/{id}/permissions` | GET (per item) | Content-level rules | **yes** (same) | ✅ implemented (`permissions` module) |
| View / flow permissions + flow default templates | GET (per item) | Remaining permission surfaces | **yes** | 💤 backlog |

## REST — automation & schedules

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/tasks/extractRefreshes` | GET (paginated) | Extract refresh tasks: target, frequency, consecutive failures | — | ✅ implemented (`automation` module) |
| `/api/{v}/sites/{site}/jobs` | GET (paginated) | Background job history: status, timestamps | — | ✅ implemented (`automation` module) |
| `/api/{v}/sites/{site}/subscriptions` | GET (paginated) | Subscriptions (incl. to zombie content) | **yes** (subscriber → U-####) | ✅ implemented (`automation` module) |
| `/api/{v}/sites/{site}/dataAlerts` | GET (paginated) | Data-driven alerts | **yes** (user) | 💤 backlog |

## REST — security posture & platform config

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/webhooks` | GET | Integration inventory, dead endpoints | — | 💤 backlog |
| `/api/{v}/sites/{site}/connected-applications` | GET | Embedding/JWT trust inventory | — | 💤 backlog |
| `/api/{v}/sites/{site}` | GET | Site settings: extract encryption, quotas, guest | — | 💤 backlog |
| `/api/{v}/sites/{site}/favorites/{userId}` | GET (per user) | Engagement signal | **yes** | 💤 backlog |

## VizQL Data Service — Admin Insights (time dimension)

Before querying each datasource, the collector calls
`POST /api/v1/vizql-data-service/read-metadata` (✅ implemented) to learn which
field captions the datasource actually exposes — the curated field lists in the
manifest are intersected with reality, since captions drift across Tableau
releases. `tca verify` uses the same call to check VDS access at kick-off.

| Datasource queried | Endpoint | Purpose | PII | Status |
|---|---|---|---|---|
| TS Events | `POST /api/v1/vizql-data-service/query-datasource` | Event log: sign-ins, views, publishes (90/365d window); rows also accumulate into `history.events` (dedup on Event Id — outlives retention) | minimized — identity columns (`Actor User Name`, `Item Owner Email`) never requested; numeric ids kept as join keys | ✅ implemented (`activity` module) |
| TS Users | same | Last login beyond 90d, license role, activity aggregates | **yes** — `User LUID`/`User Name`/`User Email`/`User Friendly Name` → U-#### via vault | ✅ implemented (`activity` module) |
| Site Content | same | Last Accessed At — the zombie-detection backbone | minimized — `Owner Email`, `Item Parent Project Owner Email`, `Description` never requested | ✅ implemented (`activity` module) |
| Groups | same | Membership cross-check vs REST | **yes** — `User LUID` → U-####; `User Email` never requested | ✅ implemented (`activity` module) |
| Permissions | same | Cross-validation of permission data (Tableau's own user × item × capability rows) | **yes** — `User LUID` → U-####; `User Email`/`Grantee Name`/`Grantee LUID` never requested | ✅ implemented (`activity` module) |
| Subscriptions | same | Delivery health: status, last sent, consecutive failures | minimized — subscriber/creator/owner identity fields and free-text `Subject` never requested (REST already collects them) | ✅ implemented (`activity` module) |
| Tokens | same | PAT hygiene: stale tokens, leaver-owned tokens | **yes** — `Owner Email` → U-#### via reverse vault lookup (unknown → `[redacted]`); `Database User Name`/`Device Name` never requested | ✅ implemented (`activity` module) |
| Job Performance | same | Refresh failure rates, durations, queue delay | **yes** — `Owner Email` → U-#### via reverse lookup; `Error Message`/`Subscriber Email`/Bridge fields never requested | ✅ implemented (`activity` module) |
| Viz Load Times | same | View/datasource load durations and failures | minimized — owner e-mails, `HTTP User Agent` and `HTTP Request URI` never requested | ✅ implemented (`activity` module) |

Fallback chain (same package schema regardless of path): VDS → Hyper extract download → guided CSV import. Status: 💤 backlog.

## Metadata API (GraphQL)

The two query texts are versioned in the PII manifest
([`src/tca/pseudo/manifest.py`](https://github.com/simboli/tableau-cloud-audit/blob/main/src/tca/pseudo/manifest.py),
`GRAPHQL_MANIFEST`) — they are the complete request surface, and they ask for
no user identity fields at all (owners come from REST, already pseudonymised).
Cursor pagination, 20 nodes per page; `tca verify` probes the endpoint with a
`totalCount`-only query at kick-off.

| Query area | Endpoint | Purpose | PII | Status |
|---|---|---|---|---|
| `graphql:datasources` — published datasources → fields (incl. calculated-field formulas) → upstream tables/databases | `POST /api/metadata/graphql` | Duplicate-metric input; lineage (blast radius) | minimized — no user fields requested | ✅ implemented (`metadata` module) |
| `graphql:workbooks` — workbooks → sheets/dashboards → fields used; embedded datasources (fields, formulas, upstream tables); upstream published datasources | same | Field usage per sheet (duplicate detection); embedded-vs-published governance signal; lineage | minimized — no user fields requested | ✅ implemented (`metadata` module) |
| Data quality warnings & labels | same | Existing governance signals (needs Data Management) | — | 💤 backlog |
