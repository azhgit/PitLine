"use client";

import { useEffect, useState } from "react";

const DMS_BASE_URL = process.env.NEXT_PUBLIC_DMS_BASE_URL ?? "http://localhost:8001";
const DMS_API_KEY = process.env.NEXT_PUBLIC_DMS_API_KEY ?? "";

type Appointment = {
  id: number;
  technician_id: number;
  time_slot: string;
  customer_name: string;
  customer_phone: string;
  vehicle_make: string;
  vehicle_model: string;
  vehicle_year: number;
  urgency: "urgent" | "non_urgent";
  status: string;
};

type Handoff = {
  id: number;
  customer_phone: string;
  reason: string;
  created_at: string;
};

type ConnectionState = "connecting" | "connected" | "reconnecting";

function authedUrl(path: string): string {
  const url = new URL(path, DMS_BASE_URL);
  url.searchParams.set("api_key", DMS_API_KEY);
  return url.toString();
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const REASON_LABELS: Record<string, string> = {
  urgent_no_availability: "No Master Technician available",
  cannot_understand_caller: "Could not understand caller",
  cannot_verify_caller: "Could not verify caller",
};

export default function Page() {
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [loadError, setLoadError] = useState<string | null>(null);
  // Flags rows that arrived live (via SSE) vs. the initial history fetch,
  // so a fresh row gets a highlight. State, not a ref: read during render
  // (ref.current can't be - see react-hooks/refs), so it needs to be part
  // of the render output React tracks.
  const [liveIds, setLiveIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;

    async function loadHistory() {
      try {
        const [appointmentsRes, handoffsRes] = await Promise.all([
          fetch(authedUrl("/appointments")),
          fetch(authedUrl("/handoffs")),
        ]);
        if (!appointmentsRes.ok || !handoffsRes.ok) {
          throw new Error(`DMS returned ${appointmentsRes.status}/${handoffsRes.status}`);
        }
        const [appointmentsData, handoffsData]: [Appointment[], Handoff[]] = await Promise.all([
          appointmentsRes.json(),
          handoffsRes.json(),
        ]);
        if (cancelled) return;
        setAppointments([...appointmentsData].reverse());
        setHandoffs(handoffsData);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to reach the DMS");
        }
      }
    }

    loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const source = new EventSource(authedUrl("/events"));

    source.onopen = () => setConnection("connected");
    // The browser's EventSource retries on its own (part of the SSE spec) -
    // this just reflects that state in the UI rather than implementing
    // reconnection logic itself.
    source.onerror = () => setConnection("reconnecting");

    source.onmessage = (event) => {
      const { type, data } = JSON.parse(event.data) as
        | { type: "appointment_created"; data: Appointment }
        | { type: "handoff_created"; data: Handoff };

      setLiveIds((prev) => new Set(prev).add(`${type}:${data.id}`));

      if (type === "appointment_created") {
        setAppointments((prev) => [data, ...prev]);
      } else if (type === "handoff_created") {
        setHandoffs((prev) => [data, ...prev]);
      }
    };

    return () => source.close();
  }, []);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <header className="border-b border-neutral-800 px-6 py-4">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">PitLine Ops Dashboard</h1>
            <p className="text-sm text-neutral-400">
              Read-only live view of DMS activity - every row here came from the agent, not from
              this screen.
            </p>
          </div>
          <ConnectionBadge state={connection} />
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        {loadError && (
          <div className="mb-6 rounded-md border border-red-900 bg-red-950/50 px-4 py-3 text-sm text-red-300">
            Couldn&apos;t load history from the DMS: {loadError}
          </div>
        )}

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <section>
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
              New Appointments ({appointments.length})
            </h2>
            <div className="space-y-2">
              {appointments.length === 0 && <EmptyState label="No appointments yet." />}
              {appointments.map((a) => (
                <AppointmentCard
                  key={a.id}
                  appointment={a}
                  isLive={liveIds.has(`appointment_created:${a.id}`)}
                />
              ))}
            </div>
          </section>

          <section>
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
              Safety Transfers ({handoffs.length})
            </h2>
            <div className="space-y-2">
              {handoffs.length === 0 && <EmptyState label="No handoffs yet." />}
              {handoffs.map((h) => (
                <HandoffCard
                  key={h.id}
                  handoff={h}
                  isLive={liveIds.has(`handoff_created:${h.id}`)}
                />
              ))}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const config = {
    connected: { label: "Live", dot: "bg-green-500" },
    connecting: { label: "Connecting…", dot: "bg-yellow-500" },
    reconnecting: { label: "Reconnecting…", dot: "bg-yellow-500 animate-pulse" },
  }[state];

  return (
    <div className="flex items-center gap-2 text-sm text-neutral-400">
      <span className={`h-2 w-2 rounded-full ${config.dot}`} />
      {config.label}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <p className="text-sm text-neutral-500">{label}</p>;
}

function AppointmentCard({
  appointment,
  isLive,
}: {
  appointment: Appointment;
  isLive: boolean;
}) {
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        isLive ? "border-green-800 bg-green-950/30" : "border-neutral-800 bg-neutral-900"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{appointment.customer_name}</span>
        {appointment.urgency === "urgent" && (
          <span className="rounded bg-red-900/60 px-2 py-0.5 text-xs text-red-300">Urgent</span>
        )}
      </div>
      <p className="text-sm text-neutral-400">
        {appointment.vehicle_year} {appointment.vehicle_make} {appointment.vehicle_model} ·{" "}
        {formatTime(appointment.time_slot)}
      </p>
      <p className="mt-1 text-xs text-neutral-500">Confirmation SMS queued</p>
    </div>
  );
}

function HandoffCard({ handoff, isLive }: { handoff: Handoff; isLive: boolean }) {
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        isLive ? "border-orange-800 bg-orange-950/30" : "border-neutral-800 bg-neutral-900"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{handoff.customer_phone}</span>
        <span className="text-xs text-neutral-500">{formatTime(handoff.created_at)}</span>
      </div>
      <p className="text-sm text-neutral-400">
        {REASON_LABELS[handoff.reason] ?? handoff.reason}
      </p>
      <p className="mt-1 text-xs text-neutral-500">Handoff SMS queued</p>
    </div>
  );
}
