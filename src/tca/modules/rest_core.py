"""The identity-core module: users, groups, group membership.

Every page goes scrubber -> store; the scrubber refuses anything the PII
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
            clean = ctx.scrubber.scrub(USERS, payload)
            ctx.store.write_response(ctx.run_id, USERS, clean, page=page)
            stats.add_page(USERS)
            ctx.on_page(USERS, page)

        group_luids: list[str] = []
        for page, payload in ctx.rest.groups():
            clean = ctx.scrubber.scrub(GROUPS, payload)
            ctx.store.write_response(ctx.run_id, GROUPS, clean, page=page)
            stats.add_page(GROUPS)
            ctx.on_page(GROUPS, page)
            group_luids.extend(_group_ids(payload))

        for group_luid in group_luids:
            for page, payload in ctx.rest.group_users(group_luid):
                clean = ctx.scrubber.scrub(GROUP_USERS, payload)
                ctx.store.write_response(
                    ctx.run_id, GROUP_USERS, clean, page=page, entity_luid=group_luid
                )
                stats.add_page(GROUP_USERS)
                ctx.on_page(GROUP_USERS, page)

        return stats


def _group_ids(payload: dict[str, Any]) -> list[str]:
    groups = payload.get("groups", {}).get("group", [])
    if isinstance(groups, dict):  # single-object shape
        groups = [groups]
    return [g["id"] for g in groups if isinstance(g, dict) and "id" in g]


register(RestCoreModule())
