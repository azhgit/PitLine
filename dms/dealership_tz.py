from datetime import datetime
from zoneinfo import ZoneInfo

# Every datetime in this schema (models.py) is naive and implicitly local -
# "local" needs to consistently mean the dealership's real timezone
# (Vancouver, per this scenario), not whatever timezone the host OS
# happens to be set to. Railway's containers default to UTC, which would
# otherwise make BUSINESS_HOURS "8am-6pm" (availability.py) mean 1am-11am
# in Vancouver - the middle of the night, not the business day.
DEALERSHIP_TZ = ZoneInfo("America/Vancouver")


def local_now() -> datetime:
    """Naive wall-clock 'now' in the dealership's timezone - stripped back
    to naive on purpose, to match every other datetime already stored this
    way (time_slot, created_at).
    """
    return datetime.now(DEALERSHIP_TZ).replace(tzinfo=None)
