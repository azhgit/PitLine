import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_dms.db"
os.environ["DMS_API_KEY"] = "test-api-key"

import pytest
from fastapi.testclient import TestClient

from db import Base, SessionLocal, engine
from main import app
from models import Technician
from rate_limit import reset_rate_limits


def future_slot(day_offset: int = 1, hour: int = 9) -> str:
    """An ISO timestamp aligned to the same hour-grid availability.py scans,
    so tests can book a slot and know exactly which query it'll show up in.
    """
    day_zero = datetime.now().replace(minute=0, second=0, microsecond=0)
    return (day_zero + timedelta(days=day_offset)).replace(hour=hour).isoformat()


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clear_rate_limits():
    reset_rate_limits()


@pytest.fixture
def client():
    return TestClient(app, headers={"X-API-Key": os.environ["DMS_API_KEY"]})


@pytest.fixture
def technicians():
    """One on-call Quick Service tech, one off-call Quick Service tech, one
    on-call Master Technician."""
    db = SessionLocal()
    try:
        mike = Technician(name="Mike Alvarez", specialty="quick_service", on_call=True)
        ravi = Technician(name="Ravi Patel", specialty="quick_service", on_call=False)
        dana = Technician(name="Dana Whitfield", specialty="master_technician", on_call=True)
        db.add_all([mike, ravi, dana])
        db.commit()
        db.refresh(mike)
        db.refresh(ravi)
        db.refresh(dana)
        return {"mike": mike.id, "ravi": ravi.id, "dana": dana.id}
    finally:
        db.close()
