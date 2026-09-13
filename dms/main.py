from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI

from appointments import router as appointments_router
from auth import verify_api_key
from availability import router as availability_router
from db import Base, engine
from handoffs import router as handoffs_router
from jobs import router as jobs_router
from technicians import router as technicians_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # schema only - seed data is separate, see seed.py
    yield


app = FastAPI(title="PitLine Mock DMS", lifespan=lifespan)
# /health stays unauthenticated - platform health checks hit it without a key.
app.include_router(technicians_router, dependencies=[Depends(verify_api_key)])
app.include_router(appointments_router, dependencies=[Depends(verify_api_key)])
app.include_router(availability_router, dependencies=[Depends(verify_api_key)])
app.include_router(handoffs_router, dependencies=[Depends(verify_api_key)])
app.include_router(jobs_router, dependencies=[Depends(verify_api_key)])


@app.get("/health")
def health():
    return {"ok": True}
