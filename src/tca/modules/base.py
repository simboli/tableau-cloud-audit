"""Module protocol and registry.

A module is one extraction domain (rest_core, permissions, activity, ...).
It receives a ready-made RunContext and its only job is: fetch pages, pass
them through the scrubber, land them in the store. Modules never talk HTTP
details (transport's job) and never compute anything (analysis is out of
scope for this repo, by design).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tca.pseudo.scrubber import Scrubber
from tca.sources.metadata import MetadataApi
from tca.sources.rest import TableauRest
from tca.sources.vds import VizqlDataService
from tca.storage.writer import PackageStore
from tca.transport.client import TransportError

# The module set that runs when neither --modules nor [collect] modules is given.
# Single source of truth for the default selection (the CLI option and the config
# fall back here). Dependency ordering is applied later by resolve_modules.
DEFAULT_MODULES: tuple[str, ...] = (
    "rest_core",
    "content",
    "automation",
    "permissions",
    "activity",
    "metadata",
)


def _passthrough_track(items: Sequence[Any], label: str) -> Iterable[Any]:
    return items


@dataclass
class RunContext:
    rest: TableauRest
    store: PackageStore
    scrubber: Scrubber
    run_id: int
    vds: VizqlDataService | None = None
    metadata: MetadataApi | None = None
    # Called with (endpoint, page) after each landed page — the CLI uses it
    # for progress output; modules stay console-agnostic.
    on_page: Callable[[str, int], None] = lambda endpoint, page: None
    # Wraps a per-item loop for progress display (the CLI injects a rich-based
    # tracker with a known total; the default is a pass-through).
    track: Callable[[Sequence[Any], str], Iterable[Any]] = _passthrough_track
    # Units already landed in this run (populated on --resume; empty otherwise).
    # raw.api_responses is the checkpoint: one unit = (endpoint, entity_luid, page).
    done: set[tuple[str, str | None, int]] = field(default_factory=set)

    def is_done(self, endpoint: str, entity_luid: str | None = None, page: int = 1) -> bool:
        return (endpoint, entity_luid, page) in self.done

    def land(
        self,
        endpoint: str,
        payload: dict,  # type: ignore[type-arg]
        page: int = 1,
        entity_luid: str | None = None,
    ) -> bool:
        """Scrub + write one page, unless it already landed (resume). Returns
        True when the page was written, False when skipped."""
        if self.is_done(endpoint, entity_luid, page):
            return False
        clean = self.scrubber.scrub(endpoint, payload)
        self.store.write_response(self.run_id, endpoint, clean, page=page, entity_luid=entity_luid)
        self.done.add((endpoint, entity_luid, page))
        self.on_page(endpoint, page)
        return True


@dataclass
class ModuleStats:
    pages: dict[str, int] = field(default_factory=dict)
    skipped: int = 0  # units already landed before this run attempt (resume)
    denied: list[tuple[str, str]] = field(default_factory=list)  # (endpoint, entity_luid)
    failed: list[tuple[str, str]] = field(default_factory=list)  # 5xx after retries
    failure_streak: int = 0  # consecutive per-item server errors
    personal_space: int = 0  # Personal Space workbooks whose permissions were not queried

    def count(self, endpoint: str, written: bool) -> None:
        if written:
            self.pages[endpoint] = self.pages.get(endpoint, 0) + 1
        else:
            self.skipped += 1

    def count_denied(self, endpoint: str, entity_luid: str) -> None:
        self.denied.append((endpoint, entity_luid))

    def count_failed(self, endpoint: str, entity_luid: str) -> None:
        self.failed.append((endpoint, entity_luid))


# One item answering 5xx after every retry is a fault of that item (seen live:
# a Personal Space workbook answering 500 to its permissions query) and is
# skipped. This many in a row means Tableau itself is failing: the run aborts,
# resumable, instead of finishing "ok" with whole loops missing.
MAX_CONSECUTIVE_ITEM_FAILURES = 3


def land_item(
    ctx: RunContext,
    stats: ModuleStats,
    endpoint: str,
    entity_luid: str,
    fetch: Callable[[str], dict[str, Any]],
) -> None:
    """Fetch + land one per-item page.

    403/404 on a single item is a property of the site (e.g. Personal Space
    content refuses permission queries even to admins) — counted as denied and
    skipped. A server error that outlives the transport retries is counted as
    failed and skipped, up to MAX_CONSECUTIVE_ITEM_FAILURES in a row. Any other
    error aborts the run (resumable). Skipped items never land, so the next
    collect retries them.
    """
    if ctx.is_done(endpoint, entity_luid):
        stats.count(endpoint, written=False)
        return
    try:
        payload = fetch(entity_luid)
    except TransportError as exc:
        if exc.status_code in (403, 404):
            stats.failure_streak = 0
            stats.count_denied(endpoint, entity_luid)
            return
        if exc.status_code is not None and exc.status_code >= 500:
            stats.failure_streak += 1
            if stats.failure_streak >= MAX_CONSECUTIVE_ITEM_FAILURES:
                raise TransportError(
                    f"{stats.failure_streak} items in a row failed with a server error — "
                    f"Tableau looks unavailable, stopping. Last: {exc}",
                    status_code=exc.status_code,
                ) from exc
            stats.count_failed(endpoint, entity_luid)
            return
        raise
    stats.failure_streak = 0
    stats.count(endpoint, ctx.land(endpoint, payload, entity_luid=entity_luid))


class Module(Protocol):
    name: str
    requires: tuple[str, ...]

    def run(self, ctx: RunContext) -> ModuleStats: ...


_REGISTRY: dict[str, Module] = {}


def register(module: Module) -> None:
    _REGISTRY[module.name] = module


def resolve_modules(names: list[str]) -> list[Module]:
    """Validate names and return modules with dependencies first."""
    unknown = [n for n in names if n not in _REGISTRY]
    if unknown:
        raise ValueError(
            f"Unknown module(s): {', '.join(unknown)}. Available: {', '.join(sorted(_REGISTRY))}"
        )
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in ordered:
            return
        for dep in _REGISTRY[name].requires:
            if dep not in names:
                raise ValueError(f"Module '{name}' requires '{dep}' — add it to --modules.")
            visit(dep)
        ordered.append(name)

    for name in names:
        visit(name)
    return [_REGISTRY[n] for n in ordered]
