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
    # -- automation & schedules -------------------------------------------------
    # Extract refresh tasks and background jobs carry no user objects; the
    # subscription owner is a Tableau user -> pseudonymised.
    "/tasks/extractRefreshes": EndpointSpec(),
    "/jobs": EndpointSpec(),
    "/subscriptions": EndpointSpec(user_paths=["subscriptions.subscription[*].user"]),
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


# ============================================================================
# VizQL Data Service (Admin Insights) — row-based sources
# ============================================================================
#
# VDS lets us choose the columns at query time, so the primary privacy
# mechanism here is MINIMIZATION: identity columns we don't need (e.g.
# 'Actor User Name', 'Owner Email') are simply never requested — ``fields``
# below is the complete, exhaustive list of what the collector asks for.
#
# Numeric user ids ('Actor User Id', 'User ID', 'Item Owner Id', ...) are
# kept in the clear: they are Tableau-internal join keys that map to a person
# only through TS Users — whose identity columns ARE pseudonymised via the
# vault ('User LUID' + attributes -> U-####).


@dataclass(frozen=True)
class VdsSourceSpec:
    """One Admin Insights datasource: what to request and what to pseudonymise."""

    datasource_name: str  # display name inside the 'Admin Insights' project
    fields: tuple[str, ...]  # ONLY these captions are ever requested
    luid_column: str | None = None  # column holding the Tableau user LUID
    # vault attribute -> column caption; each listed column is replaced by U-####
    identity_attr_columns: dict[str, str] = field(default_factory=dict)
    # columns holding a user E-MAIL with no LUID in the row (Tokens, Job
    # Performance): resolved to U-#### via reverse vault lookup; e-mails not
    # in the vault become '[redacted]'. Never stored as-is.
    email_columns: tuple[str, ...] = ()


VDS_MANIFEST: dict[str, VdsSourceSpec] = {
    "vds:ts_events": VdsSourceSpec(
        datasource_name="TS Events",
        fields=(
            "Event Id",
            "Event Date",
            "Event Name",
            "Event Type",
            "Item Id",
            "Item LUID",
            "Item Type",
            "Item Name",
            "Project Name",
            "Actor User Id",  # numeric join key, not an identity
            "Actor Site Role",
            "Actor License Role",
            "Item Owner Id",  # numeric join key
            "Target User Id",  # numeric join key
        ),
    ),
    "vds:ts_users": VdsSourceSpec(
        datasource_name="TS Users",
        fields=(
            "User ID",  # numeric join key towards TS Events
            "User LUID",
            "User Name",
            "User Email",
            "User Friendly Name",
            "User Site Role",
            "User License Type",
            "User Creation Date",
            "Last Login Date",
            "Days Since Last Login",
        ),
        luid_column="User LUID",
        identity_attr_columns={
            "name": "User Name",
            "email": "User Email",
            "full_name": "User Friendly Name",
        },
    ),
    "vds:tokens": VdsSourceSpec(
        datasource_name="Tokens",
        # NOTE: 'Database User Name' (credential user) and 'Device Name' /
        # 'Device ID' (personal device identifiers) are deliberately NOT
        # requested. 'PAT Name' is user-chosen but is how admins recognise
        # tokens — kept, same policy as content names.
        fields=(
            "GUID",
            "Token Identifier",
            "Token Type",
            "PAT Name",
            "Issued At",
            "Expires At",
            "Last Used At",
            "Last Updated",
            "Database Type",
            "Owner Email",  # -> U-#### via reverse vault lookup (see email_columns)
        ),
        email_columns=("Owner Email",),
    ),
    "vds:job_performance": VdsSourceSpec(
        datasource_name="Job Performance",
        # NOTE: 'Error Message' (may embed credentials/e-mails), 'Subscriber
        # Email', 'Subscription Subject', 'Parent Project Owner Email' and the
        # Bridge* fields are deliberately NOT requested.
        fields=(
            "Job ID",
            "Job LUID",
            "Job Type",
            "Job Result",
            "Final Job Result",
            "Was Manual Run",
            "Item ID",
            "Item LUID",
            "Item Type",
            "Item Name",
            "Parent Project Name",
            "Schedule LUID",
            "Schedule Name",
            "Created At",
            "Queued At",
            "Started At",
            "Completed At",
            "Job Duration",
            "Job Queued Duration",
            "Job Execution Duration",
            "Job Overflow Queued Duration",
            "Was Overflow Queued",
            "Extract File Size",
            "Subscriber ID",  # numeric join key, not an identity
            "Owner Email",  # -> U-#### via reverse vault lookup
        ),
        email_columns=("Owner Email",),
    ),
    "vds:site_content": VdsSourceSpec(
        datasource_name="Site Content",
        # NOTE: 'Owner Email', 'Item Parent Project Owner Email' and the
        # free-text 'Description' are deliberately NOT requested.
        fields=(
            "Item ID",
            "Item LUID",
            "Item Type",
            "Item Name",
            "Item Parent Project Name",
            "Top Parent Project Name",
            "Project Level",
            "Created At",
            "Updated At",
            "First Published At",
            "Last Published At",
            "Last Accessed At",
            "Size (bytes)",
            "Is Data Extract",
            "Has Refresh Scheduled",
            "Data Source Is Certified",
            "Data Source Database Type",
            "View Workbook ID",
            "View Type",
            "Controlled Permissions Enabled",
            "Controlling Permissions Project LUID",
            "Tags",
        ),
    ),
}
