import asyncio
import json
import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

CHANNEL = "pitline_events"


class EventBroadcaster:
    """Ops Dashboard's SSE feed (CONTEXT.md). Local fan-out to this
    process's connected dashboard tabs is a plain asyncio.Queue set -
    subscribe()/unsubscribe()/publish()'s signatures are unchanged from
    the original in-process version. What's different: publish() sends a
    Postgres NOTIFY instead of writing directly to the local queues, and
    start()'s LISTEN connection is what actually delivers to them - both
    this replica's own writes and every other replica's. One DMS replica's
    booking reaches a dashboard tab connected to a different replica; see
    ADR-0003 for why this (not Redis) is the fix.

    No-ops on SQLite (the test suite - see conftest.py): asyncpg only
    speaks Postgres, and nothing in the test suite exercises /events.
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._conn: asyncpg.Connection | None = None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def start(self) -> None:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url.startswith("postgres"):
            return
        self._conn = await asyncpg.connect(database_url)
        await self._conn.add_listener(CHANNEL, self._on_notify)
        logger.info(f"listening for Postgres NOTIFY on channel '{CHANNEL}'")

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _on_notify(self, connection, pid, channel, payload) -> None:
        # asyncpg's listener callback is sync, not a coroutine - queues are
        # unbounded, so put_nowait can't block or fail under normal load.
        for queue in list(self._subscribers):
            queue.put_nowait(payload)

    async def publish(self, event_type: str, data: dict) -> None:
        message = json.dumps({"type": event_type, "data": data})
        if self._conn is None:
            return
        await self._conn.execute("SELECT pg_notify($1, $2)", CHANNEL, message)


broadcaster = EventBroadcaster()
