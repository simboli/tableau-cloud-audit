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
| `/api/2.4/serverinfo` | GET | Resolve REST API version dynamically (unauthenticated) | — | 🔜 next |
| `/api/{v}/auth/signin` | POST | PAT sign-in → session token + site LUID | — | 🔜 next |
| `/api/{v}/auth/signout` | POST | Clean session termination at end of run | — | 🔜 next |

## REST — identity core (MVP milestone)

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/users?fields=_all_` | GET (paginated) | All users: site role, last login, auth setting, … | **yes** (name, fullName, email, externalAuthUserId) | 🔜 next |
| `/api/{v}/sites/{site}/groups` | GET (paginated) | All groups (names stay in the clear by design) | no | 🔜 next |
| `/api/{v}/sites/{site}/groups/{group}/users` | GET (paginated, per group) | Group membership edges | **yes** (user objects) | 🔜 next |

## REST — content inventory

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/projects` | GET (paginated) | Project tree, LockedToProject/ManagedByOwner, owners | **yes** (owner) | 📋 planned |
| `/api/{v}/sites/{site}/workbooks?fields=_all_` | GET (paginated) | Workbooks: owner, project, size, updatedAt, tags | **yes** (owner) | 📋 planned |
| `/api/{v}/sites/{site}/views?includeUsageStatistics=true` | GET (paginated) | Views + all-time view counts | — | 📋 planned |
| `/api/{v}/sites/{site}/datasources?fields=_all_` | GET (paginated) | Data sources: isCertified, hasExtracts, owner | **yes** (owner) | 📋 planned |
| `/api/{v}/sites/{site}/workbooks/{id}/connections` | GET (per item) | Connection types, servers, embedded credentials | **yes** (connection username presence) | 📋 planned |
| `/api/{v}/sites/{site}/datasources/{id}/connections` | GET (per item) | Same, for data sources | **yes** | 📋 planned |
| `/api/{v}/sites/{site}/flows` | GET (paginated) | Prep flows inventory | **yes** (owner) | 📋 planned |
| `/api/{v}/sites/{site}/virtualConnections` | GET (paginated) | Virtual connections (REST is the only inventory) | **yes** (owner) | 💤 backlog |

## REST — permissions

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/projects/{id}/permissions` | GET (per project) | Explicit project-level rules | **yes** (user grantees) | 📋 planned |
| `/api/{v}/sites/{site}/projects/{id}/default-permissions/{type}` | GET (per project × type) | Default templates children inherit | **yes** | 📋 planned |
| `/api/{v}/sites/{site}/workbooks/{id}/permissions` (+ datasources, views, flows) | GET (per item — expensive!) | Content-level rules | **yes** | 📋 planned |

## REST — automation & schedules

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/tasks/extractRefreshes` | GET (paginated) | Extract refresh tasks: target, frequency, failures | — | 📋 planned |
| `/api/{v}/sites/{site}/jobs?filter=…` | GET (paginated) | Job history: status, duration, failure notes | — | 📋 planned |
| `/api/{v}/sites/{site}/subscriptions` | GET (paginated) | Subscriptions (incl. to zombie content) | **yes** (user) | 📋 planned |
| `/api/{v}/sites/{site}/dataAlerts` | GET (paginated) | Data-driven alerts | **yes** (user) | 💤 backlog |

## REST — security posture & platform config

| Endpoint | Method | Purpose | PII | Status |
|---|---|---|---|---|
| `/api/{v}/sites/{site}/webhooks` | GET | Integration inventory, dead endpoints | — | 💤 backlog |
| `/api/{v}/sites/{site}/connected-applications` | GET | Embedding/JWT trust inventory | — | 💤 backlog |
| `/api/{v}/sites/{site}` | GET | Site settings: extract encryption, quotas, guest | — | 💤 backlog |
| `/api/{v}/sites/{site}/favorites/{userId}` | GET (per user) | Engagement signal | **yes** | 💤 backlog |

## VizQL Data Service — Admin Insights (time dimension)

| Datasource queried | Endpoint | Purpose | PII | Status |
|---|---|---|---|---|
| TS Events | `POST /api/v1/vizql-data-service/query-datasource` | Event log: sign-ins, views, publishes (90/365d window) | **yes** (actor) | 📋 planned |
| TS Users | same | Last login beyond 90d, license role, activity aggregates | **yes** | 📋 planned |
| Site Content | same | Last Accessed At — the zombie-detection backbone | **yes** (owner email) | 📋 planned |
| Groups | same | Cross-check vs REST | **yes** | 💤 backlog |
| Permissions | same | Cross-validation of permission data | **yes** | 💤 backlog |
| Subscriptions | same | — | **yes** | 💤 backlog |
| Tokens | same | PAT hygiene: stale tokens, leaver-owned tokens | **yes** | 💤 backlog |
| Job Performance | same | Refresh failure rates, durations, queue delay | — | 📋 planned |
| Viz Load Times | same | Context only | — | 💤 backlog |

Fallback chain (same package schema regardless of path): VDS → Hyper extract download → guided CSV import. Status: 💤 backlog.

## Metadata API (GraphQL)

| Query area | Endpoint | Purpose | PII | Status |
|---|---|---|---|---|
| Workbooks → sheets → fields | `POST /api/metadata/graphql` | Field usage per dashboard (duplicate detection input) | — | 📋 planned |
| Calculated fields + formulas | same | Formula extraction (normalisation/hash is mechanical, stays collector-side) | — | 📋 planned |
| Lineage (db → table → ds → wb) | same | Downstream counts, blast radius input | — | 📋 planned |
| Data quality warnings & labels | same | Existing governance signals | — | 💤 backlog |
