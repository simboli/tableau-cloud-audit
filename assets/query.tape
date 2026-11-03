# VHS tape for the "query the export" README GIF.
# Render with:  vhs docs/assets/query.tape   (repo root, .env present).
# Records against the LIVE export — aggregate results only, no PII on screen.

Output docs/assets/query.gif

Set FontSize 15
Set Width 1100
Set Height 640
Set Padding 12
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

Hide
Type "source .env && source .venv/bin/activate && clear"
Enter
Sleep 1s
Show

Type "tca export --force"
Sleep 500ms
Enter
Sleep 7s

Type "duckdb simboli-dev-820124-export.duckdb -readonly"
Sleep 500ms
Enter
Sleep 2s

Type "SELECT g.name AS grantee, r.capability, count(*) AS items"
Enter
Type "FROM state.v_permission_rules_current r"
Enter
Type "JOIN state.v_groups_current g ON g.group_luid = r.grantee_id"
Enter
Type "WHERE r.grantee_type = 'group' AND r.mode = 'Allow'"
Enter
Type "GROUP BY ALL ORDER BY items DESC LIMIT 5;"
Sleep 500ms
Enter
Sleep 5s

Sleep 3s
