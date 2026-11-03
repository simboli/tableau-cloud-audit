# VHS tape for the README demo GIF.
# Render with:  vhs docs/assets/demo.tape   (run from the repo root,
# with .env present — the demo records a LIVE run against the sandbox).

Output docs/assets/demo.gif

Set FontSize 15
Set Width 1100
Set Height 640
Set Padding 12
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "source .env && source .venv/bin/activate && clear"
Enter
Sleep 1s
Show

Type "tca verify"
Sleep 500ms
Enter
Sleep 9s

Type "tca collect --modules rest_core"
Sleep 500ms
Enter
Sleep 14s

Type "tca peek users"
Sleep 500ms
Enter
Sleep 5s

Sleep 3s
