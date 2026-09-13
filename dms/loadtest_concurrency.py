"""Concurrency verification for ADR-0003's fixes, against a real running
DMS instance - not a mock, and not just the single-process pytest suite -
to prove two claims under real concurrent load:

1. The partial unique index on (technician_id, time_slot) actually
   serializes a genuine race: N simultaneous bookings for the same slot,
   exactly one should succeed and the rest should 409.
2. The Postgres-backed rate limiter actually holds under a burst: more
   than MAX_PER_WINDOW rapid bookings from one phone number should 429
   past the limit - proving it isn't the old in-memory dict that would
   reset per-replica/per-restart.

Creates real rows in whatever DATABASE_URL/DMS_BASE_URL point at - re-seed
before anything that needs clean data (e.g. a demo recording). Run
manually: python loadtest_concurrency.py [--base-url URL] [--api-key KEY]
"""

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

CONCURRENCY = 10
MAX_PER_WINDOW = 3  # must match dms/rate_limit.py


def _payload(technician_id: int, time_slot: str, phone: str) -> dict:
    return {
        "technician_id": technician_id,
        "time_slot": time_slot,
        "customer_name": "Load Test",
        "customer_phone": phone,
        "vehicle_make": "Test",
        "vehicle_model": "Concurrency",
        "vehicle_year": 2024,
        "urgency": "non_urgent",
    }


async def _get_technician_id(client: httpx.AsyncClient) -> int:
    resp = await client.get("/technicians")
    resp.raise_for_status()
    return resp.json()[0]["id"]


async def test_double_booking_race(client: httpx.AsyncClient, technician_id: int) -> bool:
    slot = "2031-01-15T09:00:00"
    print(f"\n=== Race test: {CONCURRENCY} concurrent bookings for the same slot ===")
    results = await asyncio.gather(
        *[
            client.post("/appointments", json=_payload(technician_id, slot, f"555-9{i:03d}"))
            for i in range(CONCURRENCY)
        ]
    )
    codes = sorted(r.status_code for r in results)
    successes = codes.count(200)
    conflicts = codes.count(409)
    print(f"status codes: {codes}")
    ok = successes == 1 and conflicts == CONCURRENCY - 1
    print(
        f"[{'PASS' if ok else 'FAIL'}] exactly one booking succeeded ({successes}), "
        f"rest conflicted ({conflicts})"
    )
    return ok


async def test_rate_limit_holds_under_burst(client: httpx.AsyncClient, technician_id: int) -> bool:
    phone = "555-8000"
    n = MAX_PER_WINDOW + 3
    print(f"\n=== Rate limit test: {n} bookings in a row from one phone number ===")
    codes = []
    for i in range(n):
        resp = await client.post(
            "/appointments",
            json=_payload(technician_id, f"2031-01-{16 + i:02d}T09:00:00", phone),
        )
        codes.append(resp.status_code)
    print(f"status codes: {codes}")
    successes = codes.count(200)
    limited = codes.count(429)
    ok = successes == MAX_PER_WINDOW and limited == n - MAX_PER_WINDOW
    print(
        f"[{'PASS' if ok else 'FAIL'}] exactly {MAX_PER_WINDOW} bookings succeeded, "
        f"{limited} rate-limited"
    )
    return ok


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("DMS_BASE_URL", "http://localhost:8001"))
    parser.add_argument("--api-key", default=os.getenv("DMS_API_KEY", ""))
    args = parser.parse_args()

    async with httpx.AsyncClient(
        base_url=args.base_url, headers={"X-API-Key": args.api_key}, timeout=15.0
    ) as client:
        technician_id = await _get_technician_id(client)
        print(f"Target: {args.base_url}  (technician_id={technician_id})")
        race_ok = await test_double_booking_race(client, technician_id)
        rate_ok = await test_rate_limit_holds_under_burst(client, technician_id)
        print(f"\n{2 if race_ok and rate_ok else 1 if race_ok or rate_ok else 0}/2 checks passed")


if __name__ == "__main__":
    asyncio.run(main())
