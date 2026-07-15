"""The PII manifest: which fields, in which endpoint, contain user identity.

THIS FILE IS THE PRIVACY CONTRACT OF THE COLLECTOR.

If you are security-reviewing this project, this is the single place that
answers "what counts as personal data, per API endpoint". The scrubber
(`tca/pseudo/scrubber.py`) enforces it mechanically:

* every endpoint the collector calls MUST be registered here — payloads from
  unregistered endpoints are refused, never written;
* at each ``user_paths`` location the scrubber expects Tableau user objects
  and replaces every identity attribute (``USER_IDENTITY_FIELDS``) with the
  user's stable ``U-####`` pseudonym;
* after scrubbing, a safety net rejects any payload that still contains an
  e-mail-shaped string or a known user LUID — a gap in this manifest fails
  loudly instead of silently writing PII.

Path syntax: dot-separated keys into the JSON payload; a ``[*]`` suffix means
"each element of this array". Example: ``users.user[*]`` are the user objects
in a ``GET /users`` response page.
"""

from dataclasses import dataclass, field

# Attributes of a Tableau REST user object that identify a person.
# Each one, when present, is replaced by the user's pseudonym.
USER_IDENTITY_FIELDS: tuple[str, ...] = (
    "id",  # the user LUID itself — pseudonymised, it is the join key
    "name",  # Tableau username (often the corporate e-mail)
    "fullName",
    "email",
    "externalAuthUserId",
)


@dataclass(frozen=True)
class EndpointSpec:
    """What the scrubber needs to know about one endpoint."""

    user_paths: list[str] = field(default_factory=list)


# Endpoint keys are the *templates* used by the modules (concrete LUIDs are
# passed separately and stored in raw.api_responses.entity_luid).
MANIFEST: dict[str, EndpointSpec] = {
    # -- auth & infrastructure (no user data) --------------------------------
    "/serverinfo": EndpointSpec(),
    # -- identity core (MVP) --------------------------------------------------
    "/users": EndpointSpec(user_paths=["users.user[*]"]),
    "/groups": EndpointSpec(),  # group names stay in the clear by design
    "/groups/{luid}/users": EndpointSpec(user_paths=["users.user[*]"]),
}
