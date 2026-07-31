# What is granted to "All Users"?

Broad accidental grants are how sensitive content leaks in practice — not
through attacks, but through defaults nobody reviews.

```sql
SELECT r.target_type, r.is_default_template, r.capability, r.mode, count(*) AS rules
FROM clear.permission_rules r
WHERE r.grantee = 'All Users' AND r.mode = 'Allow'
GROUP BY ALL
ORDER BY rules DESC;
```

## How to read it

Watch the `is_default_template = true` rows: those are the project **default
permissions** that every new workbook inherits — the place "open to everyone"
hides.

These are the *explicit rules*. Resolving who can actually see what, through
group expansion and project inheritance, is a much deeper computation that the
collector deliberately does not perform.

---

Needs an attached package file — see [the setup snippet](index.md#setup).
