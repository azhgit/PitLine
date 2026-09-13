# PitLine Ops Dashboard

Read-only Next.js + TypeScript screen that renders the mock DMS's live
activity - new Appointments and Handoff simulations (safety transfers) -
as they happen during a call. See [CONTEXT.md](../CONTEXT.md)'s "Ops
Dashboard" entry for the scenario this fills in the JD's "typed web stack"
requirement.

Has no write path of its own: every row it shows came from PitLine's agent
calling the DMS's API, never from this screen.

## How it gets live data

On load, it fetches the current `/appointments` and `/handoffs` history from
the DMS over plain REST, then opens a browser `EventSource` against the
DMS's `/events` Server-Sent Events endpoint (`dms/main.py`, backed by
`dms/events.py`'s in-memory broadcaster) for everything after that. The
connection badge in the header reflects `EventSource`'s own built-in
reconnect behavior - verified live by killing and restarting the DMS while
a tab stayed open, not just read from the spec.

The DMS's other endpoints authenticate via an `X-API-Key` header, but the
browser's native `EventSource` can't set custom headers, so `/events`
accepts the key as a `?api_key=` query param instead (`dms/auth.py`). That
means `NEXT_PUBLIC_DMS_API_KEY` is visible in this page's source/network
tab by design - acceptable for this demo's mock data, not a pattern to
carry into anything real.

## Running locally

```bash
npm install
npm run dev
```

Needs a `.env.local` with:

```
NEXT_PUBLIC_DMS_BASE_URL=http://localhost:8001
NEXT_PUBLIC_DMS_API_KEY=<same value as the DMS's DMS_API_KEY>
```
