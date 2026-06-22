"""Cross-cutting service errors mapped to an HTTP status by the routers.

Kept tiny and dependency-free so any service/router can import it without a cycle.
"""

from __future__ import annotations


class ServiceUnavailableError(Exception):
    """A required backend dependency (e.g. the Redis job store) is unreachable.

    Distinct from a legitimate "not found": a router maps this to **503 Service Unavailable** so an
    infrastructure outage is reported honestly, never masqueraded as a 404/empty result (which would
    make a Redis outage indistinguishable from a genuinely absent job).
    """
