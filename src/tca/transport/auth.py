"""PAT credentials and sign-in plumbing.

The PAT secret lives only in memory (the CLI reads it from the TCA_PAT_SECRET
environment variable). It is sent exclusively to the ``/auth/signin`` endpoint
body and must never appear in logs, error messages, or ``repr()`` output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Version used only for the unauthenticated /serverinfo call; every other
# request uses the restApiVersion that /serverinfo reports.
BOOTSTRAP_API_VERSION = "3.4"


@dataclass(frozen=True)
class Credentials:
    server: str  # base URL, e.g. https://eu-west-1a.online.tableau.com
    site: str  # the site contentUrl, e.g. 'acme-industries'
    pat_name: str
    pat_secret: str = field(repr=False)

    @classmethod
    def build(cls, pod: str, site: str, pat_name: str, pat_secret: str) -> Credentials:
        """Accept either a pod name ('eu-west-1a') or a full base URL."""
        server = pod if "://" in pod else f"https://{pod}.online.tableau.com"
        return cls(server=server.rstrip("/"), site=site, pat_name=pat_name, pat_secret=pat_secret)


def signin_body(creds: Credentials) -> dict[str, Any]:
    return {
        "credentials": {
            "personalAccessTokenName": creds.pat_name,
            "personalAccessTokenSecret": creds.pat_secret,
            "site": {"contentUrl": creds.site},
        }
    }
