"""The activity module: Admin Insights via VizQL Data Service.

This is the entire time dimension of the assessment — TS Events (the event
log), TS Users (real last-login beyond the REST field), Site Content (last
accessed at, the zombie-detection backbone). REST tells you *state*; these
tell you *behaviour over time*.

Field selection is minimization-first: only the captions listed in the VDS
manifest are ever requested (identity columns we don't need are never even
fetched), intersected with what the datasource actually exposes — captions
drift across Tableau releases.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tca.modules.base import ModuleStats, RunContext, register
from tca.pseudo.manifest import VDS_MANIFEST
from tca.sources.rest import DATASOURCES
from tca.transport.client import TransportError

ADMIN_INSIGHTS_PROJECT = "Admin Insights"
TS_EVENTS_ENDPOINT = "vds:ts_events"
JOB_PERFORMANCE_ENDPOINT = "vds:job_performance"


class ActivityModule:
    name = "activity"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()
        if ctx.vds is None:
            raise RuntimeError("ActivityModule needs a VizqlDataService in the RunContext.")

        # discover the Admin Insights datasources from the REST listing
        # (landed skip-safe: the content module may already have collected it)
        ai_luids: dict[str, str] = {}
        for page, payload in ctx.rest.datasources():
            stats.count(DATASOURCES, ctx.land(DATASOURCES, payload, page=page))
            ai_luids.update(_admin_insights_luids(payload))

        for endpoint, spec in VDS_MANIFEST.items():
            luid = ai_luids.get(spec.datasource_name)
            if luid is None:
                # Admin Insights not provisioned (project is created the first
                # time an admin visits it) — skip and record, never fatal
                stats.count_denied(endpoint, spec.datasource_name)
                continue
            if ctx.is_done(endpoint, luid):
                stats.count(endpoint, written=False)
            else:
                try:
                    available = ctx.vds.field_captions(luid)
                    requested = [c for c in spec.fields if c in available]
                    payload = ctx.vds.query(luid, requested)
                except TransportError as exc:
                    if exc.status_code in (403, 404):
                        # VDS access not granted on this datasource — the #1
                        # documented setup failure mode; skip and record
                        stats.count_denied(endpoint, luid)
                        continue
                    raise
                stats.count(endpoint, ctx.land(endpoint, payload, entity_luid=luid))

            if endpoint == TS_EVENTS_ENDPOINT:
                self._accumulate_history(ctx, endpoint, luid, "Event Date", ctx.store.insert_events)
            elif endpoint == JOB_PERFORMANCE_ENDPOINT:
                self._accumulate_history(
                    ctx, endpoint, luid, "Created At", ctx.store.insert_job_runs
                )

        return stats

    def _accumulate_history(
        self,
        ctx: RunContext,
        endpoint: str,
        luid: str,
        date_caption: str,
        insert: Callable[[int, list[dict[str, Any]]], int],
    ) -> None:
        """Fold the landed page into its history accumulator (dedup on the
        natural key).

        Reads back the SANITIZED payload from raw — history always derives from
        what was actually stored, and this also makes resume safe: a crash
        between landing and accumulation is healed by re-running.
        """
        payload = ctx.store.get_response(ctx.run_id, endpoint, entity_luid=luid)
        if payload is None:  # page denied/never landed in this run
            return
        # an empty extract returns one all-null placeholder row: not history
        rows = [
            row
            for row in payload.get("data", [])
            if any(value is not None for value in row.values())
        ]
        insert(ctx.run_id, rows)
        dates = [d for row in rows if (d := row.get(date_caption)) is not None]
        if dates:
            ctx.store.record_coverage(ctx.run_id, endpoint, min(dates), max(dates))


def _admin_insights_luids(payload: dict[str, Any]) -> dict[str, str]:
    items = payload.get("datasources", {}).get("datasource", [])
    if isinstance(items, dict):
        items = [items]
    return {
        i["name"]: i["id"]
        for i in items
        if isinstance(i, dict) and i.get("project", {}).get("name") == ADMIN_INSIGHTS_PROJECT
    }


register(ActivityModule())
