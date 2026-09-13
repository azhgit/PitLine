from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models import BookingAttempt

# A caller with a genuine emergency might book once; nothing legitimate needs
# more than a handful of bookings a day from the same phone number. Catches a
# malicious/looping caller spamming the technician schedule.
WINDOW = timedelta(hours=24)
MAX_PER_WINDOW = 3


def check_rate_limit(db: Session, key: str) -> bool:
    """True if `key` is still under the limit; also records this attempt.

    Postgres-backed, not an in-process dict (see ADR-0003) - this survives
    a restart and holds correctly if this service ever runs more than one
    replica, which an in-memory counter cannot. Commits its own row
    immediately, independent of the caller's transaction, so a blocked or
    later-rejected (e.g. slot conflict) attempt still counts - matching the
    original in-memory version's behavior of recording before checking.
    """
    db.add(BookingAttempt(phone=key))
    db.commit()
    cutoff = datetime.utcnow() - WINDOW
    count = (
        db.query(BookingAttempt)
        .filter(BookingAttempt.phone == key, BookingAttempt.created_at > cutoff)
        .count()
    )
    return count <= MAX_PER_WINDOW
