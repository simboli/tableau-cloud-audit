# Which content shows no access events?

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

## Before you act

The verdict is only as good as your event history. Admin Insights retains
~90 days, so **collect monthly** — `history.events` accumulates and the sweep
gets more trustworthy with every run.

Check your window before trusting the result:

```sql
SELECT min(event_date), max(event_date) FROM history.events;
```

---

Needs an attached package file — see [the setup snippet](index.md#setup).
