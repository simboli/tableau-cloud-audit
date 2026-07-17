"""The metadata module: Tableau Metadata API (GraphQL).

Everything the REST inventory cannot see: which fields each sheet actually
uses, calculated fields WITH their formulas, embedded vs published
datasource usage, and the upstream tables/databases behind each datasource.
This is the raw material for lineage and duplicate-detection analysis —
collected mechanically, never interpreted here.

Resume reads the checkpoint from raw itself: the GraphQL cursor of the last
landed page is taken from that page's stored pageInfo, so an interrupted run
continues where it stopped without refetching (or re-paying the complexity
cost of) earlier pages.
"""

from __future__ import annotations

from typing import Any

from tca.modules.base import ModuleStats, RunContext, register
from tca.pseudo.manifest import GRAPHQL_MANIFEST, GraphqlQuerySpec
from tca.transport.client import TransportError


class MetadataModule:
    name = "metadata"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()
        if ctx.metadata is None:
            raise RuntimeError("MetadataModule needs a MetadataApi in the RunContext.")

        for endpoint, spec in GRAPHQL_MANIFEST.items():
            after: str | None = None
            start_page = 1
            landed = sorted(p for (e, luid, p) in ctx.done if e == endpoint and luid is None)
            if landed:
                for _ in landed:
                    stats.count(endpoint, written=False)
                info = self._landed_page_info(ctx, endpoint, spec, landed[-1])
                if not info.get("hasNextPage"):
                    continue  # this query already completed before the interruption
                after = str(info["endCursor"])
                start_page = landed[-1] + 1

            try:
                for page, payload in ctx.metadata.paginate(
                    spec, after=after, start_page=start_page
                ):
                    stats.count(endpoint, ctx.land(endpoint, payload, page=page))
            except TransportError as exc:
                if exc.status_code in (403, 404):
                    # Metadata API not available on this site — skip and
                    # record, never fatal (every other module stands alone)
                    stats.count_denied(endpoint, "metadata-api")
                    continue
                raise
        return stats

    def _landed_page_info(
        self, ctx: RunContext, endpoint: str, spec: GraphqlQuerySpec, page: int
    ) -> dict[str, Any]:
        payload = ctx.store.get_response(ctx.run_id, endpoint, page=page)
        if payload is None:  # unreachable: the unit came from raw itself
            return {"hasNextPage": False}
        info = payload["data"][spec.connection]["pageInfo"]
        return dict(info) if isinstance(info, dict) else {"hasNextPage": False}


register(MetadataModule())
