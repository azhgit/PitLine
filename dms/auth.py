import os

from fastapi import Header, HTTPException, Query


def verify_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    """Shared-secret check for agent/dashboard -> DMS calls - this mock DMS
    otherwise has no authentication and is reachable by anyone who knows
    DMS_BASE_URL.

    Accepts the key as a header (the agent's httpx client) or a query param
    (the Ops Dashboard's browser-side EventSource for /events - the native
    EventSource API can't set custom headers at all, so a query param is
    the only way it can authenticate). The query-param path means the key
    is visible in the dashboard's page source/network tab - acceptable
    for this demo's mock data, not a pattern to reuse for anything real.

    Fails closed: an unset DMS_API_KEY rejects every request rather than
    skipping the check, so auth can't be silently disabled by forgetting to
    set the env var.
    """
    expected = os.environ.get("DMS_API_KEY")
    provided = x_api_key or api_key
    if not expected or provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
