import asyncio
import json


class EventBroadcaster:
    """In-memory pub/sub for the Ops Dashboard's SSE feed (CONTEXT.md) - one
    asyncio.Queue per connected dashboard tab, fanned out by publish().
    In-process only, like rate_limit.py's rate limiter: fine at demo scale
    (single process), not meant to survive a restart or multiple workers.
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event_type: str, data: dict) -> None:
        message = json.dumps({"type": event_type, "data": data})
        for queue in list(self._subscribers):
            await queue.put(message)


broadcaster = EventBroadcaster()
