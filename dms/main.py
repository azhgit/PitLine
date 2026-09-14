from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from appointments import router as appointments_router
from auth import verify_api_key
from availability import router as availability_router
from db import Base, engine
from events import broadcaster
from handoffs import router as handoffs_router
from jobs import router as jobs_router
from technicians import router as technicians_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # schema only - seed data is separate, see seed.py
    await broadcaster.start()
    yield
    await broadcaster.stop()


app = FastAPI(title="PitLine Mock DMS", lifespan=lifespan)
# The dashboard is a separate deployable (its own origin) whose only access
# to the DMS is read traffic (REST fetch + this /events stream) - no
# cookies/credentials involved, so a wide-open CORS policy costs nothing
# here that verify_api_key doesn't already gate.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
)
# /health stays unauthenticated - platform health checks hit it without a key.
app.include_router(technicians_router, dependencies=[Depends(verify_api_key)])
app.include_router(appointments_router, dependencies=[Depends(verify_api_key)])
app.include_router(availability_router, dependencies=[Depends(verify_api_key)])
app.include_router(handoffs_router, dependencies=[Depends(verify_api_key)])
app.include_router(jobs_router, dependencies=[Depends(verify_api_key)])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/events", dependencies=[Depends(verify_api_key)])
async def events(request: Request):
    """Ops Dashboard live feed (CONTEXT.md) - one Server-Sent Event per new
    Appointment or Handoff. Read-only: this endpoint only ever emits events
    written by appointments.py/handoffs.py, it has no write path of its own.
    """
    queue = broadcaster.subscribe()

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await queue.get()
                yield {"event": "message", "data": message}
        finally:
            broadcaster.unsubscribe(queue)

    return EventSourceResponse(stream())
