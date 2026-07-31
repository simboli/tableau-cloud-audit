# How much content is certified?

A one-number governance pulse: how much of what people consume is blessed.

```sql
SELECT count(*) AS datasources,
       sum(CASE WHEN is_certified THEN 1 ELSE 0 END) AS certified,
       round(100.0 * certified / datasources, 1) AS pct
FROM state.v_content_current
WHERE item_type = 'datasource';
```

## How to read it

A low percentage is not automatically bad — certification is only meaningful if
your organisation actually runs a certification process. Read it as a question
rather than a score: *of the datasources people build on, how many has anyone
vouched for?*

---

Needs an attached package file — see [the setup snippet](index.md#setup).
