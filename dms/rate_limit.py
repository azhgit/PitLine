import time
from collections import defaultdict

# A caller with a genuine emergency might book once; nothing legitimate needs
# more than a handful of bookings a day from the same phone number. Catches a
# malicious/looping caller spamming the technician schedule.
WINDOW_SECONDS = 24 * 60 * 60
MAX_PER_WINDOW = 3

# In-memory, single-process only - matches the booking-conflict check's
# demo-scale tradeoff in appointments.py. Swap for a shared store (Redis) if
# this ever runs multi-process.
_attempts: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(key: str) -> bool:
    """True if `key` is still under the limit; also records this attempt."""
    now = time.monotonic()
    recent = [t for t in _attempts[key] if now - t < WINDOW_SECONDS]
    recent.append(now)
    _attempts[key] = recent
    return len(recent) <= MAX_PER_WINDOW


def reset_rate_limits() -> None:
    _attempts.clear()
