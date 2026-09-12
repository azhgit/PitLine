import os

from fastapi import Header, HTTPException


def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Shared-secret check for agent -> DMS calls - this mock DMS otherwise has
    no authentication and is reachable by anyone who knows DMS_BASE_URL.

    Fails closed: an unset DMS_API_KEY rejects every request rather than
    skipping the check, so auth can't be silently disabled by forgetting to
    set the env var.
    """
    expected = os.environ.get("DMS_API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
