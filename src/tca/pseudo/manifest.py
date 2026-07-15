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
    """What the scrubber needs to know about one endpoint.

    ``user_paths`` locate Tableau *user objects* (pseudonymised via the vault).
    ``redact_paths`` locate scalar fields that may identify a person but are
    NOT Tableau users (e.g. database credential usernames): they carry no LUID
    to map, so their value is replaced with ``[redacted]`` — presence is
    preserved, identity is not.
    """

    user_paths: list[str] = field(default_factory=list)
    redact_paths: list[str] = field(default_factory=list)


# Endpoint keys are the *templates* used by the modules (concrete LUIDs are
# passed separately and stored in raw.api_responses.entity_luid).
MANIFEST: dict[str, EndpointSpec] = {
    # -- auth & infrastructure (no user data) --------------------------------
    "/serverinfo": EndpointSpec(),
    # -- identity core (MVP) --------------------------------------------------
    "/users": EndpointSpec(user_paths=["users.user[*]"]),
    "/groups": EndpointSpec(),  # group names stay in the clear by design
    "/groups/{luid}/users": EndpointSpec(user_paths=["users.user[*]"]),
    # -- content inventory ----------------------------------------------------
    # Owners are Tableau user objects nested in each content item; project,
    # workbook and datasource NAMES stay in the clear by design.
    "/projects": EndpointSpec(user_paths=["projects.project[*].owner"]),
    "/workbooks": EndpointSpec(user_paths=["workbooks.workbook[*].owner"]),
    "/views": EndpointSpec(user_paths=["views.view[*].owner"]),
    "/datasources": EndpointSpec(user_paths=["datasources.datasource[*].owner"]),
    # Connection userName is the DATABASE credential user (often a personal
    # account, no Tableau LUID): redacted, keeping the presence signal.
    "/workbooks/{luid}/connections": EndpointSpec(
        redact_paths=["connections.connection[*].userName"]
    ),
    "/datasources/{luid}/connections": EndpointSpec(
        redact_paths=["connections.connection[*].userName"]
    ),
    # -- permissions -----------------------------------------------------------
    # Users appear as grantees (granteeCapabilities[*].user) and as the owner
    # of the target item; GROUP grantees stay in the clear by design.
    "/projects/{luid}/permissions": EndpointSpec(
        user_paths=[
            "permissions.granteeCapabilities[*].user",
            "permissions.project.owner",
        ]
    ),
    "/projects/{luid}/default-permissions/workbooks": EndpointSpec(
        user_paths=[
            "permissions.granteeCapabilities[*].user",
            "permissions.project.owner",
        ]
    ),
    "/projects/{luid}/default-permissions/datasources": EndpointSpec(
        user_paths=[
            "permissions.granteeCapabilities[*].user",
            "permissions.project.owner",
        ]
    ),
    "/workbooks/{luid}/permissions": EndpointSpec(
        user_paths=[
            "permissions.granteeCapabilities[*].user",
            "permissions.workbook.owner",
        ]
    ),
    "/datasources/{luid}/permissions": EndpointSpec(
        user_paths=[
            "permissions.granteeCapabilities[*].user",
            "permissions.datasource.owner",
        ]
    ),
}
