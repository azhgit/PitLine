"""Day 3: drains the DMS's jobs queue (Follow-up job, CONTEXT.md) and sends
each one as a real Twilio SMS. Polls rather than anything push-based -
matches ADR-0002's choice of a plain Postgres table over a broker; a poll
loop is the simplest possible drain for a single job type at this scale.

Goes through the DMS's HTTP API, not a direct DB connection, same as
agent/bot.py - see ADR-0001 (the DMS is reached only through its API, never
touched at the storage layer directly).

Run manually / as its own process: python worker.py
"""

import os
import re
import time

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

DMS_BASE_URL = os.getenv("DMS_BASE_URL", "http://localhost:8000")
POLL_INTERVAL_SECS = float(os.getenv("WORKER_POLL_INTERVAL_SECS", "5"))
MAX_ATTEMPTS = 3
TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def normalize_to_e164(phone: str) -> str | None:
    """US/Canada-only, matching the country-code assumption already made by
    dms/appointments.py's _normalize_phone and agent/caller_id.py's
    normalize_phone. Returns None for anything that isn't 10 or 11 (leading
    1) digits - a malformed number (e.g. a garbled STT capture of what the
    caller said) fails the send outright instead of handing it to Twilio,
    which would otherwise happily text whatever string looks E.164-ish.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def send_sms(to: str, body: str) -> bool:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER")
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
    try:
        response = httpx.post(
            url,
            auth=(account_sid, auth_token),
            data={"To": to, "From": from_number, "Body": body},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Twilio SMS send to {to} failed: {e}")
        return False
    return True


def process_job(client: httpx.Client, job: dict) -> None:
    payload = job["payload"]
    to = normalize_to_e164(payload["to"])
    if to is None:
        # Not transient - retrying won't make a malformed number valid, so
        # this fails immediately rather than burning through MAX_ATTEMPTS.
        logger.error(
            f"job {job['id']}: '{payload['to']}' doesn't look like a real phone number, "
            "marking failed without sending"
        )
        client.patch(f"/jobs/{job['id']}", json={"status": "failed"})
        return
    if send_sms(to, payload["body"]):
        client.patch(f"/jobs/{job['id']}", json={"status": "sent"})
        logger.info(f"job {job['id']} ({job['job_type']}) sent")
        return

    # Left "pending" (not "failed") below MAX_ATTEMPTS so the next poll
    # picks it back up - no backoff at this scale (one job type, a handful
    # of jobs per demo call), see worker.py's own docstring.
    attempt_count = job["attempt_count"] + 1
    status = "failed" if attempt_count >= MAX_ATTEMPTS else "pending"
    client.patch(f"/jobs/{job['id']}", json={"status": status, "attempt_count": attempt_count})
    logger.warning(f"job {job['id']} attempt {attempt_count} failed, status={status}")


def run_once(client: httpx.Client) -> int:
    # Claims one job at a time (POST /jobs/claim, not GET ?status=pending)
    # so two worker replicas polling at the same instant can never both
    # grab the same row - the DMS's FOR UPDATE SKIP LOCKED is what actually
    # prevents the double-claim, this loop just drains it. See ADR-0003.
    processed = 0
    while True:
        response = client.post("/jobs/claim")
        response.raise_for_status()
        job = response.json()
        if job is None:
            break
        process_job(client, job)
        processed += 1
    return processed


def main():
    client = httpx.Client(
        base_url=DMS_BASE_URL,
        timeout=5.0,
        headers={"X-API-Key": os.getenv("DMS_API_KEY", "")},
    )
    logger.info(f"worker polling {DMS_BASE_URL} every {POLL_INTERVAL_SECS}s")
    while True:
        try:
            n = run_once(client)
            if n:
                logger.info(f"processed {n} job(s)")
        except httpx.HTTPError as e:
            logger.error(f"poll failed: {e}")
        time.sleep(POLL_INTERVAL_SECS)


if __name__ == "__main__":
    main()
