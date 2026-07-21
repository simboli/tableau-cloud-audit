"""The mandatory scrub gate on the write path.

Nothing reaches the package file without passing through ``Scrubber.scrub``:
it walks the payload locations declared in the manifest, swaps every user
identity attribute for the stable ``U-####`` pseudonym (upserting the identity
vault), and then runs a blocking safety net over the result.

Fail-loud philosophy: an endpoint missing from the manifest, a user object
without an ``id``, an e-mail-shaped string or a known LUID surviving the
scrub — each of these raises ``ScrubError`` and the payload is never written.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from typing import Any

from tca.pseudo.manifest import (
    GRAPHQL_MANIFEST,
    GRAPHQL_USER_IDENTITY_FIELDS,
    MANIFEST,
    USER_IDENTITY_FIELDS,
    VDS_MANIFEST,
    VdsSourceSpec,
)
from tca.storage.writer import PackageStore

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Admin Insights uses the literal string "NA" in a LUID column when the row has
# no user (e.g. a group grantee in the Permissions datasource). It is NOT a user
# LUID: pseudonymising it would poison the identity vault with a 2-char token and
# trip the substring safety net on every subsequent payload. Treat it as absent.
_NON_USER_LUID = frozenset({"NA"})


class ScrubError(RuntimeError):
    """A privacy violation was about to happen; the write was blocked."""


class Scrubber:
    """Scrubs API payloads for one collection run."""

    def __init__(self, store: PackageStore, run_id: int) -> None:
        self._store = store
        self._run_id = run_id

    def scrub(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a pseudonymised deep copy of ``payload``, or raise ScrubError."""
        if endpoint.startswith("vds:"):
            return self._scrub_vds(endpoint, payload)
        if endpoint.startswith("graphql:"):
            return self._scrub_graphql(endpoint, payload)
        spec = MANIFEST.get(endpoint)
        if spec is None:
            raise ScrubError(
                f"Endpoint '{endpoint}' is not registered in the PII manifest "
                "(tca/pseudo/manifest.py). Refusing to write its payload."
            )
        sanitized = copy.deepcopy(payload)
        for path in spec.user_paths:
            for user_obj in _iter_objects(sanitized, path):
                self._pseudonymise(user_obj, endpoint=endpoint, path=path)
        for path in spec.redact_paths:
            _redact(sanitized, path)
        self._safety_net(endpoint, sanitized)
        return sanitized

    def _scrub_vds(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Row-based scrub for VizQL Data Service results ({"data": [row, ...]})."""
        spec: VdsSourceSpec | None = VDS_MANIFEST.get(endpoint)
        if spec is None:
            raise ScrubError(
                f"VDS source '{endpoint}' is not registered in the PII manifest "
                "(tca/pseudo/manifest.py). Refusing to write its payload."
            )
        sanitized = copy.deepcopy(payload)
        rows = sanitized.get("data", [])
        for row in rows:
            if not isinstance(row, dict):
                raise ScrubError(f"VDS row in '{endpoint}' is not an object.")
            if spec.luid_column is not None:
                luid = row.get(spec.luid_column)
                if isinstance(luid, str) and luid.strip() in _NON_USER_LUID:
                    luid = None
                attrs = {
                    attr: value
                    for attr, column in spec.identity_attr_columns.items()
                    if isinstance(value := row.get(column), str) and value
                }
                if luid is None and not attrs:
                    # all-null placeholder row (an empty extract returns one) —
                    # nothing to pseudonymise, nothing that can identify
                    continue
                if not isinstance(luid, str) or not luid:
                    raise ScrubError(
                        f"VDS row in '{endpoint}' has no '{spec.luid_column}' — "
                        "cannot pseudonymise, refusing to write."
                    )
                pseudonym = self._store.upsert_identity(self._run_id, user_luid=luid, **attrs)
                row[spec.luid_column] = pseudonym
                for column in spec.identity_attr_columns.values():
                    if column in row:
                        row[column] = pseudonym
            for column in spec.email_columns:
                value = row.get(column)
                if isinstance(value, str) and value:
                    # reverse vault lookup; unknown e-mails never survive
                    row[column] = self._store.pseudonym_by_email(value) or "[redacted]"
        self._safety_net(endpoint, sanitized)
        return sanitized

    def _scrub_graphql(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Metadata API results. The queries request no identity fields by
        construction (minimization — see the manifest), so this is mostly the
        safety net; ``user_paths`` handles GraphQL user shapes if ever used."""
        spec = GRAPHQL_MANIFEST.get(endpoint)
        if spec is None:
            raise ScrubError(
                f"GraphQL query '{endpoint}' is not registered in the PII manifest "
                "(tca/pseudo/manifest.py). Refusing to write its payload."
            )
        sanitized = copy.deepcopy(payload)
        for path in spec.user_paths:
            for user_obj in _iter_objects(sanitized, path):
                self._pseudonymise_graphql(user_obj, endpoint=endpoint, path=path)
        self._safety_net(endpoint, sanitized)
        return sanitized

    # -- internals -------------------------------------------------------------

    def _pseudonymise(self, user_obj: dict[str, Any], endpoint: str, path: str) -> None:
        luid = user_obj.get("id")
        if not isinstance(luid, str) or not luid:
            raise ScrubError(
                f"User object at '{path}' in '{endpoint}' has no 'id' — "
                "cannot pseudonymise, refusing to write."
            )
        pseudonym = self._store.upsert_identity(
            self._run_id,
            user_luid=luid,
            name=_get_str(user_obj, "name"),
            full_name=_get_str(user_obj, "fullName"),
            email=_get_str(user_obj, "email"),
            external_auth_user_id=_get_str(user_obj, "externalAuthUserId"),
        )
        for attr in USER_IDENTITY_FIELDS:
            if attr in user_obj:
                user_obj[attr] = pseudonym

    def _pseudonymise_graphql(self, user_obj: dict[str, Any], endpoint: str, path: str) -> None:
        """GraphQL user objects carry the same identities under different
        names: 'luid' (not 'id'), 'username' (not 'name'), 'name' (display
        name). Same vault, same pseudonyms as the REST shape."""
        luid = user_obj.get("luid")
        if not isinstance(luid, str) or not luid:
            raise ScrubError(
                f"User object at '{path}' in '{endpoint}' has no 'luid' — "
                "cannot pseudonymise, refusing to write."
            )
        pseudonym = self._store.upsert_identity(
            self._run_id,
            user_luid=luid,
            name=_get_str(user_obj, "username"),
            full_name=_get_str(user_obj, "name"),
            email=_get_str(user_obj, "email"),
        )
        for attr in GRAPHQL_USER_IDENTITY_FIELDS:
            if attr in user_obj:
                user_obj[attr] = pseudonym

    def _safety_net(self, endpoint: str, sanitized: dict[str, Any]) -> None:
        """Blocking last line of defence against manifest gaps."""
        text = json.dumps(sanitized)
        match = _EMAIL_RE.search(text)
        if match:
            raise ScrubError(
                f"Safety net: an e-mail-shaped string survived scrubbing in "
                f"'{endpoint}'. The PII manifest is likely missing a field for "
                "this endpoint. Nothing was written."
            )
        for luid in self._store.known_luids():
            if luid in text:
                raise ScrubError(
                    f"Safety net: known user LUID '{luid[:8]}…' survived scrubbing "
                    f"in '{endpoint}'. The PII manifest is likely missing a path "
                    "for this endpoint. Nothing was written."
                )


def _get_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) and value else None


def _redact(payload: dict[str, Any], path: str) -> None:
    """Replace the scalar at ``path`` (e.g. 'connections.connection[*].userName')
    with '[redacted]' wherever it is present and non-empty."""
    parent_path, _, field_name = path.rpartition(".")
    for obj in _iter_objects(payload, parent_path):
        value = obj.get(field_name)
        if isinstance(value, str) and value:
            obj[field_name] = "[redacted]"


def _iter_objects(payload: Any, path: str) -> Iterator[dict[str, Any]]:
    """Yield the dict(s) addressed by a manifest path like 'users.user[*]'.

    Missing keys are fine (an empty page has no user array); a scalar where an
    object is expected is not — that means the manifest and the payload shape
    disagree, and we fail loudly.
    """
    nodes: list[Any] = [payload]
    for segment in path.split("."):
        is_array = segment.endswith("[*]")
        key = segment.removesuffix("[*]")
        next_nodes: list[Any] = []
        for node in nodes:
            if not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if is_array:
                # Tableau REST sometimes returns a single object instead of a list.
                items = value if isinstance(value, list) else [value]
                next_nodes.extend(items)
            else:
                next_nodes.append(value)
        nodes = next_nodes
    for node in nodes:
        if not isinstance(node, dict):
            raise ScrubError(
                f"Manifest path '{path}' addressed a non-object value "
                f"({type(node).__name__}) — payload shape does not match the manifest."
            )
        yield node
