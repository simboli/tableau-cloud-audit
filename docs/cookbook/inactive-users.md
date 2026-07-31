# Who hasn't signed in for 90+ days?

The first pass at licence waste: seats paying for nobody.

```sql
SELECT u.user_pseudo, u.site_role, u.last_login_at,
       date_diff('day', u.last_login_at, current_timestamp::TIMESTAMP) AS days_inactive
FROM state.v_users_current u
WHERE u.last_login_at IS NULL
   OR u.last_login_at < current_timestamp::TIMESTAMP - INTERVAL 90 DAY
ORDER BY days_inactive DESC NULLS FIRST;
```

Swap `state.v_users_current` for `clear.users` to see real names.

## Before you act

Two caveats that matter:

- `last_login_at` covers **interactive sign-ins only** — API-only service
  accounts look dead but are not. Check them against the Tokens source before
  reclaiming anything.
- REST's view counters are **all-time**, not windowed. Activity over a specific
  period needs [the event history](activity-over-time.md).

---

Needs an attached package file — see [the setup snippet](index.md#setup).
