# Where do embedded credentials live?

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

## What you are seeing

Only the **presence** of embedded credentials, never the credentials themselves:
database usernames are redacted to `[redacted]` on the write path, because they
carry no Tableau identity to pseudonymise. The signal survives, the secret does
not.

---

Needs an attached package file — see [the setup snippet](index.md#setup).
