"""The automation module: extract refresh tasks, background jobs, subscriptions.

The raw material for "scheduled refreshes serving content nobody opens":
which targets refresh on which cadence, how often jobs fail, and who
subscribed to what. Three cheap paginated listings — no per-item loops.
"""

from __future__ import annotations

from tca.modules.base import ModuleStats, RunContext, register
from tca.sources.rest import EXTRACT_REFRESH_TASKS, JOBS, SUBSCRIPTIONS


class AutomationModule:
    name = "automation"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()

        for page, payload in ctx.rest.extract_refresh_tasks():
            stats.count(EXTRACT_REFRESH_TASKS, ctx.land(EXTRACT_REFRESH_TASKS, payload, page=page))

        for page, payload in ctx.rest.jobs():
            stats.count(JOBS, ctx.land(JOBS, payload, page=page))

        for page, payload in ctx.rest.subscriptions():
            stats.count(SUBSCRIPTIONS, ctx.land(SUBSCRIPTIONS, payload, page=page))

        return stats


register(AutomationModule())
