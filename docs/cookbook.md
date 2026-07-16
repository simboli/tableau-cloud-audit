# Query cookbook

Starter recipes for the package file — every query below is tested against a
real collected file and runs as-is. They are deliberately *starting points*:
each answers a first-pass question and usually raises the next, more
interesting one.

Setup (any DuckDB client — see [Troubleshooting](troubleshooting.md) if the
file is encrypted):

```sql
ATTACH 'your-site.duckdb' AS audit (ENCRYPTION_KEY 'passphrase', READ_ONLY);
USE audit;
```

All recipes use the `v_*_current` views (latest successful run) and the
`clear.*` views where real names help — remember those resolve identities
**only on your local file**, never on an export.

## 1 · Who hasn't signed in for 90+ days?

The first pass at license waste: seats paying for nobody.

```sql
SELECT u.user_pseudo, u.site_role, u.last_login_at,
       date_diff('day', u.last_login_at, current_timestamp::TIMESTAMP) AS days_inactive
FROM state.v_users_current u
WHERE u.last_login_at IS NULL
   OR u.last_login_at < current_timestamp::TIMESTAMP - INTERVAL 90 DAY
ORDER BY days_inactive DESC NULLS FIRST;
```

Swap `state.v_users_current` for `clear.users` to see real names. Caveats
that matter before acting: `last_login_at` covers *interactive* sign-ins only
(API-only service accounts look dead but aren't), and REST's view counters
are all-time — windowed activity needs the event history below.

## 2 · What is granted to "All Users"?

Broad accidental grants are how sensitive content leaks in practice — not
through attacks, but through defaults nobody reviews.

```sql
SELECT r.target_type, r.is_default_template, r.capability, r.mode, count(*) AS rules
FROM clear.permission_rules r
WHERE r.grantee = 'All Users' AND r.mode = 'Allow'
GROUP BY ALL
ORDER BY rules DESC;
```

Watch `is_default_template = true` rows: those are the project **default
permissions** every new workbook inherits — the place "open to everyone"
hides. (Note: these are the *explicit rules*; resolving who can actually see
what through group expansion and project inheritance is a much deeper
computation.)

## 3 · Which content shows no access events?

A first zombie sweep: content nobody has opened within your collected event
window.

```sql
SELECT c.item_type, c.name, c.updated_at
FROM state.v_content_current c
LEFT JOIN history.events e
  ON e.item_luid = c.item_luid AND e.event_type = 'Access'
WHERE e.event_id IS NULL
ORDER BY c.updated_at NULLS FIRST;
```

Honest caveat: the verdict is only as good as your event history. Admin
Insights retains ~90 days, so **collect monthly** — `history.events`
accumulates and the sweep gets more trustworthy with every run. Check your
window first: `SELECT min(event_date), max(event_date) FROM history.events;`

## 4 · Who owns content but isn't in the user listing?

Owners that don't appear among site users: departed users, or system/service
accounts hidden from `/users`.

```sql
SELECT c.owner_pseudo, count(*) AS items
FROM state.v_content_current c
LEFT JOIN state.v_users_current u ON u.user_pseudo = c.owner_pseudo
WHERE u.user_pseudo IS NULL AND c.owner_pseudo IS NOT NULL
GROUP BY 1
ORDER BY items DESC;
```

Resolve a pseudonym locally with `tca resolve U-0042`.

## 5 · Where do embedded credentials live?

Connections with embedded passwords keep working when their owner leaves —
until they don't, at the worst possible moment.

```sql
SELECT c.conn_type, c.server_address, count(*) AS connections
FROM state.connections c
JOIN meta.v_latest_run r USING (run_id)
WHERE c.has_embedded_credentials
GROUP BY ALL
ORDER BY connections DESC;
```

## 6 · How much content is certified?

A one-number governance pulse: how much of what people consume is blessed.

```sql
SELECT count(*) AS datasources,
       sum(CASE WHEN is_certified THEN 1 ELSE 0 END) AS certified,
       round(100.0 * certified / datasources, 1) AS pct
FROM state.v_content_current
WHERE item_type = 'datasource';
```

## 7 · What does activity look like over time?

The event history at a glance — and the query that shows why monthly
collection pays off.

```sql
SELECT event_date::DATE AS day, event_type, count(*) AS events
FROM history.events
GROUP BY ALL
ORDER BY day, event_type;
```

---

Where to go from here: the full table-by-table guide is in
[Package file schema](package-file-schema.md). Questions like *"who can
actually see this workbook, through every group and inheritance path?"* or
*"which content is safe to archive?"* need resolution logic that goes well
beyond single queries — these recipes are the map's edge, not the territory.
