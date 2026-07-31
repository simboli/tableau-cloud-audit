# Who owns content but isn't in the user listing?

Owners that don't appear among site users: departed people, or system and
service accounts hidden from `/users`.

```sql
SELECT c.owner_pseudo, count(*) AS items
FROM state.v_content_current c
LEFT JOIN state.v_users_current u ON u.user_pseudo = c.owner_pseudo
WHERE u.user_pseudo IS NULL AND c.owner_pseudo IS NOT NULL
GROUP BY 1
ORDER BY items DESC;
```

Resolve any pseudonym locally with `tca resolve U-0042` to see who it was.

## Why it matters

Content owned by a departed user keeps working until something needs the owner —
a credential refresh, a permission change, an extract failure. That is usually
the worst possible moment to discover the owner no longer exists.

---

Needs an attached package file — see [the setup snippet](index.md#setup).
