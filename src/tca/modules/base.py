"""Module protocol and registry.

A module is one extraction domain (rest_core, permissions, activity, ...).
It receives a ready-made RunContext and its only job is: fetch pages, pass
them through the scrubber, land them in the store. Modules never talk HTTP
details (transport's job) and never compute anything (analysis is out of
scope for this repo, by design).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from tca.pseudo.scrubber import Scrubber
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore


@dataclass
class RunContext:
    rest: TableauRest
    store: PackageStore
    scrubber: Scrubber
    run_id: int
    # Called with (endpoint, page) after each landed page — the CLI uses it
    # for progress output; modules stay console-agnostic.
    on_page: Callable[[str, int], None] = lambda endpoint, page: None


@dataclass
class ModuleStats:
    pages: dict[str, int] = field(default_factory=dict)

    def add_page(self, endpoint: str) -> None:
        self.pages[endpoint] = self.pages.get(endpoint, 0) + 1


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
