import os

import httpx
from loguru import logger

from caller_id import normalize_phone

TWILIO_VERIFY_BASE = "https://verify.twilio.com/v2"
MAX_SENDS = 2  # the second is for "I didn't get the text"
MAX_FAILED_CHECKS = 2


def is_nanp(digits: str) -> bool:
    """A dialable US/Canada number: 10 digits, area code and exchange not
    starting with 0/1. Anything else never gets a code - Verify bills per
    send, and premium international numbers are the SMS-pumping target.
    """
    return len(digits) == 10 and digits[0] not in "01" and digits[3] not in "01"


class StepUp:
    """Per-call SMS step-up for a caller whose Caller ID doesn't match the
    appointment (calling from another phone, or a withheld number). The
    limits live here in code, not in the prompt, so the model can't be
    talked past them.

    No DMS lookup before sending: whether a number has an appointment is
    only revealed after the caller proves they hold that phone.
    """

    def __init__(self):
        self.phone: str | None = None  # where the last code went
        self.sends = 0
        self.failed_checks = 0
        self.verified_phone: str | None = None

    def send_error(self, phone: str) -> str | None:
        if self.sends >= MAX_SENDS:
            return "send_limit_reached"
        if not is_nanp(normalize_phone(phone)):
            return "invalid_phone"
        return None

    def record_send(self, phone: str) -> None:
        self.phone = normalize_phone(phone)
        self.sends += 1

    def check_error(self) -> str | None:
        if self.phone is None:
            return "no_code_sent"
        if self.failed_checks >= MAX_FAILED_CHECKS:
            return "too_many_attempts"
        return None

    def record_check(self, approved: bool) -> dict:
        if approved:
            self.verified_phone = self.phone
            return {"verified": True}
        self.failed_checks += 1
        return {"verified": False, "attempts_left": MAX_FAILED_CHECKS - self.failed_checks}


def _verify_auth() -> tuple[str, tuple[str, str]] | None:
    sid = os.environ.get("TWILIO_VERIFY_SERVICE_SID")
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not account_sid or not auth_token:
        return None
    return f"{TWILIO_VERIFY_BASE}/Services/{sid}", (account_sid, auth_token)


async def send_code(digits: str) -> bool:
    """Texts a Verify code to +1<digits>. Twilio owns the code, its expiry,
    and its attempt limits - nothing is stored here. False on any failure."""
    auth = _verify_auth()
    if not auth:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{auth[0]}/Verifications", data={"To": f"+1{digits}", "Channel": "sms"}, auth=auth[1]
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Twilio Verify send failed: {e}")
        return False
    return True


async def check_code(digits: str, code: str) -> bool:
    """Fails closed: an API error, an expired code, or Twilio's own attempt
    limit (a 404) all count as not approved."""
    auth = _verify_auth()
    if not auth:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{auth[0]}/VerificationCheck", data={"To": f"+1{digits}", "Code": code}, auth=auth[1]
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Twilio Verify check failed: {e}")
        return False
    return response.json().get("status") == "approved"


if __name__ == "__main__":
    assert is_nanp("7785550199") and not is_nanp("0785550199") and not is_nanp("7781550199")
    assert not is_nanp("447700900123") and not is_nanp("555")

    s = StepUp()
    assert s.check_error() == "no_code_sent"
    assert s.send_error("+44 7700 900123") == "invalid_phone"
    assert s.send_error("+1 778-555-0199") is None
    s.record_send("778-555-0199")
    s.record_send("7785550199")
    assert s.send_error("7785550199") == "send_limit_reached"

    assert s.record_check(False) == {"verified": False, "attempts_left": 1}
    assert s.verified_phone is None
    assert s.record_check(False) == {"verified": False, "attempts_left": 0}
    assert s.check_error() == "too_many_attempts"

    s = StepUp()
    s.record_send("778-555-0199")
    assert s.record_check(True) == {"verified": True} and s.verified_phone == "7785550199"
    print("ok")
