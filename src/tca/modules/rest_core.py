"""The identity-core module: users, groups, group membership.

Every page goes scrubber -> store via ctx.land(), which also skips units that
already landed when a run is resumed. The scrubber refuses anything the PII
manifest does not cover, so this module cannot leak identities even by
mistake.
"""

from __future__ import annotations

from typing import Any

from tca.modules.base import ModuleStats, RunContext, register
from tca.sources.rest import GROUP_USERS, GROUPS, USERS


class RestCoreModule:
    name = "rest_core"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()

        for page, payload in ctx.rest.users():
            stats.count(USERS, ctx.land(USERS, payload, page=page))

        group_luids: list[str] = []
        for page, payload in ctx.rest.groups():
            stats.count(GROUPS, ctx.land(GROUPS, payload, page=page))
            group_luids.extend(_group_ids(payload))

        for group_luid in group_luids:
            # resume: skip the whole per-group call if its first page landed
            if ctx.is_done(GROUP_USERS, group_luid):
                stats.count(GROUP_USERS, written=False)
                continue
            for page, payload in ctx.rest.group_users(group_luid):
                stats.count(
                    GROUP_USERS, ctx.land(GROUP_USERS, payload, page=page, entity_luid=group_luid)
                )

        return stats


def _group_ids(payload: dict[str, Any]) -> list[str]:
    groups = payload.get("groups", {}).get("group", [])
    if isinstance(groups, dict):  # single-object shape
        groups = [groups]
    return [g["id"] for g in groups if isinstance(g, dict) and "id" in g]


register(RestCoreModule())
