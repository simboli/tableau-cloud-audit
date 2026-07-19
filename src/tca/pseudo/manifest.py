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

Three request surfaces, three sections below: REST endpoints (``MANIFEST``),
VizQL Data Service sources (``VDS_MANIFEST``) and Metadata API GraphQL
queries (``GRAPHQL_MANIFEST``). The latter two practice minimization first:
what is not in their specs is never even requested.
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
    "vds:groups": VdsSourceSpec(
        datasource_name="Groups",
        # NOTE: 'User Email' is deliberately NOT requested (the pseudonymised
        # LUID is the only user reference we need — attributes are already in
        # the vault via /users and TS Users); 'Site LUID'/'Site Name'
        # (constant) and the aggregate count fields are skipped too.
        fields=(
            "Group LUID",
            "Group Name",
            "Group Minimum Site Role",
            "Group Is Licensed On Site",
            "User LUID",
        ),
        luid_column="User LUID",
    ),
    "vds:permissions": VdsSourceSpec(
        datasource_name="Permissions",
        # Tableau's own user × item × capability flattening — collected as
        # cross-validation data (the collector evaluates nothing).
        # NOTE: 'User Email' and 'Grantee Name' (mixed user e-mails / group
        # names) are deliberately NOT requested; 'Grantee LUID'/'Grantee Type'
        # are skipped as well — a user grantee's LUID would carry identity,
        # and rule grantees are already collected (pseudonymised) by the REST
        # permissions module.
        fields=(
            "Item LUID",
            "Item Name",
            "Item Type",
            "Item Parent Project Name",
            "Top Parent Project Name",
            "Controlling Permissions Project Name",
            "Capability Type",
            "Permission Value",
            "Permissions Description",
            "Has Permission?",
            "User LUID",
            "User Site Role",
        ),
        luid_column="User LUID",
    ),
    "vds:subscriptions": VdsSourceSpec(
        datasource_name="Subscriptions",
        # Delivery-health complement to REST /subscriptions (which already
        # collects subscriber and subject, pseudonymised).
        # NOTE: 'Subscriber Email'/'Subscriber User LUID', 'Created By User
        # Email'/'Created By User LUID', 'Item Owner Email', the free-text
        # 'Subject' and the metric-follower 'Subscriber Group *' fields are
        # deliberately NOT requested.
        fields=(
            "Subscription LUID",
            "Subscription ID",
            "Subscription Status",
            "Data Conditions",
            "Has Image Attached",
            "Has PDF Attached",
            "Is Extract Refresh Triggered",
            "Item LUID",
            "Item Type",
            "Item Name",
            "Schedule LUID",
            "Schedule Name",
            "Schedule Type",
            "Created At",
            "Last Sent",
            "Task LUID",
            "Task Type",
            "Consecutive Failure Count",
            "Historical Queue Time",
            "Historical Run Time",
        ),
    ),
    "vds:viz_load_times": VdsSourceSpec(
        datasource_name="Viz Load Times",
        # NOTE: 'Item Owner Email', 'Project Owner User Name' and 'Workbook
        # Owner User Name' (e-mails), 'HTTP User Agent' (device fingerprint)
        # and 'HTTP Request URI' (may embed personal filter values) are
        # deliberately NOT requested. Upstream caption is 'Item Luid' (sic).
        fields=(
            "Request ID",
            "Request Time",
            "Duration",
            "Status Code",
            "Status Code Type",
            "Item Luid",
            "Item Type",
            "Item Name",
            "Item Repository URL",
            "Project Name",
            "Workbook Name",
        ),
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


# ============================================================================
# Metadata API (GraphQL) — lineage, field usage, calculated-field formulas
# ============================================================================
#
# Same primary mechanism as VDS: MINIMIZATION at query time. The query texts
# below are the complete, exhaustive request surface of the metadata module —
# nothing else is ever asked of the Metadata API — and they request NO user
# identity fields at all: owners are already collected (pseudonymised) by the
# REST endpoints, so the GraphQL queries simply never mention them.
#
# Calculated-field FORMULAS are collected deliberately (duplicate-metric
# detection needs them) and are treated as content, not personal data — same
# policy as workbook and project names (maintainer's decision, 2026-07-17).
#
# ``user_paths`` is the safety valve for the day a query needs a user object:
# the scrubber pseudonymises GraphQL user shapes (luid/username/name/email)
# through the same vault as REST user objects. Today every list is empty by
# construction.

# Attributes of a Metadata API user object (GraphQL shape differs from REST:
# 'luid' not 'id', 'username' not 'name', 'name' is the display name).
GRAPHQL_USER_IDENTITY_FIELDS: tuple[str, ...] = ("luid", "username", "name", "email")


@dataclass(frozen=True)
class GraphqlQuerySpec:
    """One versioned Metadata API query and how to scrub its result."""

    connection: str  # root connection field, e.g. 'workbooksConnection'
    query: str  # the COMPLETE query text — the whole request surface
    user_paths: list[str] = field(default_factory=list)


# Shared field selection: typed field attributes, plus the formula on
# calculated fields. Field 'id' is the Metadata API's internal node id (not a
# LUID, carries no identity) — it is the join key between a sheet's
# datasourceFields and the datasource's own field list.
_FIELD_SELECTION = """
        id
        name
        isHidden
        __typename
        ... on CalculatedField { formula role dataType }
        ... on ColumnField { role dataType }
"""

GRAPHQL_MANIFEST: dict[str, GraphqlQuerySpec] = {
    "graphql:datasources": GraphqlQuerySpec(
        connection="publishedDatasourcesConnection",
        query=f"""
query tca_datasources($first: Int, $after: String) {{
  publishedDatasourcesConnection(first: $first, after: $after) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      id
      luid
      name
      projectName
      hasExtracts
      extractLastRefreshTime
      isCertified
      containsUnsupportedCustomSql
      fields {{{_FIELD_SELECTION}      }}
      upstreamTables {{
        id name schema fullName isEmbedded
        database {{ id name connectionType }}
      }}
    }}
  }}
}}
""",
    ),
    "graphql:workbooks": GraphqlQuerySpec(
        connection="workbooksConnection",
        query=f"""
query tca_workbooks($first: Int, $after: String) {{
  workbooksConnection(first: $first, after: $after) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      id
      luid
      name
      projectName
      containsUnsupportedCustomSql
      upstreamDatasources {{ id luid name }}
      embeddedDatasources {{
        id
        name
        fields {{{_FIELD_SELECTION}        }}
        upstreamTables {{
          id name schema fullName isEmbedded
          database {{ id name connectionType }}
        }}
      }}
      sheets {{
        id
        name
        worksheetFields {{ id name }}
        datasourceFields {{ id name __typename }}
      }}
      dashboards {{ id name path sheets {{ id }} }}
    }}
  }}
}}
""",
    ),
}
