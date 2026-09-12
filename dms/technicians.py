from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db
from models import Technician

router = APIRouter(prefix="/technicians", tags=["technicians"])


def _serialize(t: Technician) -> dict:
    return {"id": t.id, "name": t.name, "specialty": t.specialty, "on_call": t.on_call}


@router.get("")
def list_technicians(db: Session = Depends(get_db)):
    return [_serialize(t) for t in db.query(Technician).all()]


@router.get("/{technician_id}")
def get_technician(technician_id: int, db: Session = Depends(get_db)):
    t = db.get(Technician, technician_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(t)
