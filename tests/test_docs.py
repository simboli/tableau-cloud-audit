"""Docs drift guards: transparency pages must track the code, not lag it."""

import re
from pathlib import Path

import typer

from tca.cli import app
from tca.pseudo.manifest import GRAPHQL_MANIFEST, MANIFEST, VDS_MANIFEST

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
COMMANDS = typer.main.get_command(app).commands  # type: ignore[attr-defined]

# Every place a reader is told to type a command: docs, top-level guides, issue forms.
USER_FACING = [
    *sorted(ROOT.glob("*.md")),
    *sorted(DOCS.glob("*.md")),
    *sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")),
]
# `tca <command>` followed by its arguments, stopping at shell operators/comments/backticks.
INVOCATION = re.compile(r"\btca ([a-z][a-z-]*)((?:[ \t]+[^\s`&|;#]+)*)")


def test_what_we_collect_mentions_every_endpoint() -> None:
    text = (DOCS / "what-we-collect.md").read_text(encoding="utf-8")
    missing = [e for e in MANIFEST if e != "/serverinfo" and f"`{e}`" not in text]
    missing += [e for e in VDS_MANIFEST if f"{e}" not in text]
    missing += [e for e in GRAPHQL_MANIFEST if f"{e}" not in text]
    assert not missing, (
        "docs/what-we-collect.md does not mention: "
        + ", ".join(missing)
        + " — update the transparency page in the same PR that touches the manifest."
    )


def test_api_coverage_mentions_every_vds_source() -> None:
    text = (DOCS / "api-coverage.md").read_text(encoding="utf-8")
    missing = [
        spec.datasource_name for spec in VDS_MANIFEST.values() if spec.datasource_name not in text
    ]
    assert not missing, "docs/api-coverage.md does not mention: " + ", ".join(missing)


def test_api_coverage_mentions_every_graphql_query() -> None:
    text = (DOCS / "api-coverage.md").read_text(encoding="utf-8")
    missing = [e for e in GRAPHQL_MANIFEST if e not in text]
    assert not missing, "docs/api-coverage.md does not mention: " + ", ".join(missing)


def test_cli_reference_documents_every_command() -> None:
    text = (DOCS / "cli-reference.md").read_text(encoding="utf-8")
    missing = [
        name
        for name in COMMANDS
        if f"| `tca {name}" not in text or not re.search(rf"^## `tca {name}\b", text, re.M)
    ]
    assert not missing, (
        "docs/cli-reference.md lacks an 'At a glance' row and/or a section for: "
        + ", ".join(missing)
    )


def test_documented_invocations_use_real_options() -> None:
    bad = []
    for path in USER_FACING:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in INVOCATION.finditer(line):
                command = COMMANDS.get(m.group(1))
                if command is None:  # prose such as "another tca run in progress"
                    continue
                known = {o for p in command.params for o in (*p.opts, *p.secondary_opts)}
                known.add("--help")
                flags = [t.split("=")[0] for t in m.group(2).split() if t.startswith("-")]
                bad += [
                    f"{path.relative_to(ROOT)}:{lineno}: `{m.group(0).strip()}` ({f})"
                    for f in flags
                    if f not in known
                ]
    assert not bad, "documented flags that the CLI does not accept:\n" + "\n".join(bad)
