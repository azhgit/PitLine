import os

from fastapi import Header, HTTPException, Query, Request


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    """Two shared secrets, split by what they can do:

    - DMS_API_KEY: full access, header only. Held by the agent and worker,
      never by a browser.
    - DASHBOARD_API_KEY: GET only. The Ops Dashboard ships it in its
      browser bundle (NEXT_PUBLIC_*), so it must be treated as public - it
      can read the schedule but can't book, cancel, claim jobs, or write
      call records. Also accepted as a query param, because the dashboard's
      EventSource for /events can't set headers.

    Before this split, the single key the dashboard exposed could cancel
    anyone's appointment. Fails closed: an unset key accepts nothing.
    """
    full = os.environ.get("DMS_API_KEY")
    read = os.environ.get("DASHBOARD_API_KEY")
    if full and x_api_key == full:
        return
    if request.method == "GET" and read and (x_api_key or api_key) == read:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def verify_review_key(x_review_key: str | None = Header(default=None)) -> None:
    """Separate secret for reading call transcripts. DMS_API_KEY ships to
    the dashboard's browser bundle (see verify_api_key), so anything it can
    read is effectively public; transcripts must not be. Header only - no
    query-param fallback - and fails closed like verify_api_key.
    """
    expected = os.environ.get("REVIEW_API_KEY")
    if not expected or x_review_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing review key")
