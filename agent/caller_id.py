import os
import re

import httpx
from loguru import logger

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def normalize_phone(value: str) -> str:
    """Digits only, dropping a leading US/Canada country code - so Twilio's
    E.164 "+15550428871" matches a caller-stated "555-042-8871". Mirrors
    dms/appointments.py's _normalize_phone (kept separate, not shared
    between the two services - see that copy's docstring for why the
    country-code strip matters, not just punctuation).
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


async def lookup_caller_number(call_sid: str | None) -> str | None:
    """The real inbound Caller ID for a Twilio call, via the REST API.

    Twilio's Media Stream start event carries CallSid but never the From
    number itself (verified against pipecat's TwiML template and start-event
    parsing - neither includes it), so getting a caller-supplied phone
    string is the only alternative, and that's exactly the unverified input
    this lookup exists to avoid trusting.

    Fails closed: any missing credentials, missing call_sid, or API error
    returns None. Callers must treat None as "could not verify this caller,"
    not "skip verification."
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not call_sid:
        return None
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls/{call_sid}.json"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, auth=(account_sid, auth_token))
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Twilio caller-ID lookup failed: {e}")
        return None
    return response.json().get("from")
