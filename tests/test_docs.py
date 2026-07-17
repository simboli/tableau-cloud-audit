"""Docs drift guards: transparency pages must track the code, not lag it."""

from pathlib import Path

from tca.pseudo.manifest import GRAPHQL_MANIFEST, MANIFEST, VDS_MANIFEST

DOCS = Path(__file__).parent.parent / "docs"


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
