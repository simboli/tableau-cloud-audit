# What does activity look like over time?

The event history at a glance — and the query that shows why monthly collection
pays off.

```sql
SELECT event_date::DATE AS day, event_type, count(*) AS events
FROM history.events
GROUP BY ALL
ORDER BY day, event_type;
```

## Why the shape matters

Admin Insights retains roughly 90 days. Every run folds fresh events into
`history.events`, deduplicated on natural keys, so the accumulated history
outlives Tableau's retention window.

Gaps in the day series are real information, not noise: they mark periods where
no run covered the window. `tca summary` reports them explicitly rather than
letting a hole pass for a quiet week.

---

Needs an attached package file — see [the setup snippet](index.md#setup).
