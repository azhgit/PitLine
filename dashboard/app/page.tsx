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

// summary: verification/tool_results are written by the agent's code;
// summary/caller_stated are the LLM's words (agent/bot.py transfer_and_end_call).
type HandoffSummaryData = {
  summary: string | null;
  caller_stated: { vehicle: string | null; symptom: string | null };
  verification: { caller_id_verified: boolean; method: string };
  tool_results: { tool: string; args: Record<string, unknown>; result: Record<string, unknown> }[];
};

type Handoff = {
  id: number;
  customer_phone: string | null; // null = caller couldn't be verified
  reason: string;
  summary: HandoffSummaryData | null; // null on handoffs logged before summaries existed
  created_at: string;
};

const UNVERIFIED_CALLER = "Unverified caller";

// Aggregates only (dms/calls.py stats) - transcripts need a separate key
// this page never has.
type CallStats = {
  total: number;
  by_outcome: Record<string, number>;
  containment_by_rule: number | null;
  containment_judged: number | null;
  hallucination_rate: number | null;
  reviewed: number;
};

// Calls are written when they end and don't come over SSE, so stats poll.
const CALL_STATS_POLL_MS = 30_000;

type Technician = {
  id: number;
  name: string;
  specialty: "quick_service" | "master_technician";
  on_call: boolean;
};

const SPECIALTY_LABELS: Record<string, string> = {
  quick_service: "Quick Service",
  master_technician: "Master Technician",
};

type ConnectionState = "connecting" | "connected" | "reconnecting";

function authedUrl(path: string): string {
  const url = new URL(path, DMS_BASE_URL);
  url.searchParams.set("api_key", DMS_API_KEY);
  return url.toString();
}

// appointment.time_slot is a naive wall-clock string already in the
// dealership's timezone (America/Vancouver - see dms/dealership_tz.py),
// not UTC. Parsing it with `new Date(iso)` directly would have the
// browser reinterpret those digits in the VIEWER's own timezone -
// correct by coincidence only if they happen to also be in Vancouver,
// wrong (off by the UTC offset) for anyone else, which defeats the point
// of a demo recording anyone could be watching. Building the Date via
// Date.UTC and formatting with timeZone: "UTC" keeps the digits fixed
// regardless of where the dashboard is opened from.
function formatDealershipWallClock(iso: string): string {
  const [datePart, timePart] = iso.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const fixed = new Date(Date.UTC(year, month - 1, day, hour, minute));
  const formatted = fixed.toLocaleString("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `${formatted} PT`;
}

// handoff.created_at is a real UTC instant (Postgres's now(), models.py's
// server_default) serialized without a trailing "Z" by the ORM - unlike
// time_slot above, this one genuinely needs converting to Vancouver time,
// not just relabeling.
function formatUtcAsDealershipTime(iso: string): string {
  const utcIso = iso.endsWith("Z") ? iso : `${iso}Z`;
  const formatted = new Date(utcIso).toLocaleString("en-US", {
    timeZone: "America/Vancouver",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `${formatted} PT`;
}

const REASON_LABELS: Record<string, string> = {
  urgent_no_availability: "No Master Technician available",
  cannot_understand_caller: "Could not understand caller",
  cannot_verify_caller: "Could not verify caller",
  verification_failed: "Wrong verification code",
};

const STATUS_LABELS: Record<string, string> = {
  booked: "Booked",
  completed: "Completed",
  cancelled: "Cancelled",
};

type Selection =
  | { type: "appointment"; appointment: Appointment; technician: Technician | undefined }
  | { type: "handoff"; handoff: Handoff };

export default function Page() {
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [loadError, setLoadError] = useState<string | null>(null);
  // Flags rows that arrived live (via SSE) vs. the initial history fetch,
  // so a fresh row gets a highlight. State, not a ref: read during render
  // (ref.current can't be - see react-hooks/refs), so it needs to be part
  // of the render output React tracks.
  const [liveIds, setLiveIds] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection | null>(null);
  const [callStats, setCallStats] = useState<CallStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadStats() {
      try {
        const res = await fetch(authedUrl("/calls/stats"));
        if (res.ok && !cancelled) setCallStats(await res.json());
      } catch {
        // Non-critical panel: keep the last good numbers.
      }
    }
    loadStats();
    const timer = setInterval(loadStats, CALL_STATS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    // Re-fetched on every (re)connect, not just on mount - EventSource
    // doesn't replay events missed while disconnected, so without this a
    // booking made during a DMS outage would never appear until a manual
    // page refresh. REST is the source of truth here; each call replaces
    // state outright rather than merging, so a missed-then-caught-up gap
    // can't produce duplicates.
    async function loadHistory() {
      try {
        const [appointmentsRes, handoffsRes, techniciansRes] = await Promise.all([
          fetch(authedUrl("/appointments")),
          fetch(authedUrl("/handoffs")),
          fetch(authedUrl("/technicians")),
        ]);
        if (!appointmentsRes.ok || !handoffsRes.ok || !techniciansRes.ok) {
          throw new Error(
            `DMS returned ${appointmentsRes.status}/${handoffsRes.status}/${techniciansRes.status}`
          );
        }
        const [appointmentsData, handoffsData, techniciansData]: [
          Appointment[],
          Handoff[],
          Technician[],
        ] = await Promise.all([appointmentsRes.json(), handoffsRes.json(), techniciansRes.json()]);
        if (cancelled) return;
        setAppointments([...appointmentsData].reverse());
        setHandoffs(handoffsData);
        setTechnicians(techniciansData);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to reach the DMS");
        }
      }
    }

    const source = new EventSource(authedUrl("/events"));

    source.onopen = () => {
      setConnection("connected");
      loadHistory();
    };
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

    return () => {
      cancelled = true;
      source.close();
    };
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

        {callStats && <CallStatsPanel stats={callStats} />}

        <section className="mb-10">
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
            Technician Schedule
          </h2>
          {technicians.length === 0 ? (
            <EmptyState label="No technicians yet." />
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {technicians.map((tech) => (
                <TechnicianColumn
                  key={tech.id}
                  technician={tech}
                  appointments={appointments
                    .filter((a) => a.technician_id === tech.id && a.status === "booked")
                    .sort((a, b) => a.time_slot.localeCompare(b.time_slot))}
                  liveIds={liveIds}
                  onSelectAppointment={(appointment) =>
                    setSelection({ type: "appointment", appointment, technician: tech })
                  }
                />
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
            Safety Transfers ({handoffs.length})
          </h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {handoffs.length === 0 && <EmptyState label="No handoffs yet." />}
            {handoffs.map((h) => (
              <HandoffCard
                key={h.id}
                handoff={h}
                isLive={liveIds.has(`handoff_created:${h.id}`)}
                onSelect={() => setSelection({ type: "handoff", handoff: h })}
              />
            ))}
          </div>
        </section>
      </main>

      {selection && <SummaryModal selection={selection} onClose={() => setSelection(null)} />}
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

function pct(value: number | null): string {
  return value === null ? "-" : `${Math.round(value * 100)}%`;
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-neutral-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-neutral-500">{hint}</p>}
    </div>
  );
}

function CallStatsPanel({ stats }: { stats: CallStats }) {
  const count = (outcome: string) => stats.by_outcome[outcome] ?? 0;
  return (
    <section className="mb-10">
      <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
        Calls ({stats.total})
      </h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Containment" value={pct(stats.containment_by_rule)} hint="By recorded outcome" />
        <StatTile
          label="Containment, judged"
          value={pct(stats.containment_judged)}
          hint={`LLM judge, ${stats.reviewed} reviewed`}
        />
        <StatTile label="Transferred" value={String(count("transferred"))} />
        <StatTile label="Caller hung up" value={String(count("abandoned"))} />
      </div>
    </section>
  );
}

function EmptyState({ label }: { label: string }) {
  return <p className="text-sm text-neutral-500">{label}</p>;
}

function TechnicianColumn({
  technician,
  appointments,
  liveIds,
  onSelectAppointment,
}: {
  technician: Technician;
  appointments: Appointment[];
  liveIds: Set<string>;
  onSelectAppointment: (appointment: Appointment) => void;
}) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/50">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-3">
        <div>
          <p className="font-medium">{technician.name}</p>
          <p className="text-xs text-neutral-400">{SPECIALTY_LABELS[technician.specialty]}</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-neutral-400">
          <span
            className={`h-2 w-2 rounded-full ${
              technician.on_call ? "bg-green-500" : "bg-neutral-600"
            }`}
          />
          {technician.on_call ? "On call" : "Off call"}
        </div>
      </div>
      <div className="space-y-2 p-3">
        {appointments.length === 0 && <EmptyState label="No upcoming appointments." />}
        {appointments.map((a) => (
          <AppointmentCard
            key={a.id}
            appointment={a}
            isLive={liveIds.has(`appointment_created:${a.id}`)}
            onSelect={() => onSelectAppointment(a)}
          />
        ))}
      </div>
    </div>
  );
}

function AppointmentCard({
  appointment,
  isLive,
  onSelect,
}: {
  appointment: Appointment;
  isLive: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`cursor-pointer rounded-lg border px-4 py-3 transition-colors hover:border-neutral-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 ${
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
        {formatDealershipWallClock(appointment.time_slot)}
      </p>
      <p className="mt-1 text-xs text-neutral-500">Confirmation SMS queued</p>
    </div>
  );
}

function HandoffCard({
  handoff,
  isLive,
  onSelect,
}: {
  handoff: Handoff;
  isLive: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`cursor-pointer rounded-lg border px-4 py-3 transition-colors hover:border-neutral-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 ${
        isLive ? "border-orange-800 bg-orange-950/30" : "border-neutral-800 bg-neutral-900"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{handoff.customer_phone ?? UNVERIFIED_CALLER}</span>
        <span className="text-xs text-neutral-500">{formatUtcAsDealershipTime(handoff.created_at)}</span>
      </div>
      <p className="text-sm text-neutral-400">
        {REASON_LABELS[handoff.reason] ?? handoff.reason}
      </p>
      <p className="mt-1 text-xs text-neutral-500">Handoff SMS queued</p>
    </div>
  );
}

function SummaryModal({ selection, onClose }: { selection: Selection; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const title =
    selection.type === "appointment" ? selection.appointment.customer_name : (selection.handoff.customer_phone ?? UNVERIFIED_CALLER);

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Summary for ${title}`}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-neutral-800 bg-neutral-900 p-5 shadow-xl"
      >
        <div className="mb-4 flex items-start justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
          >
            ✕
          </button>
        </div>

        {selection.type === "appointment" ? (
          <AppointmentSummary appointment={selection.appointment} technician={selection.technician} />
        ) : (
          <HandoffSummary handoff={selection.handoff} />
        )}
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-neutral-800 py-2 last:border-none">
      <span className="text-xs uppercase tracking-wide text-neutral-500">{label}</span>
      <span className="text-right text-sm text-neutral-100">{value}</span>
    </div>
  );
}

function AppointmentSummary({
  appointment,
  technician,
}: {
  appointment: Appointment;
  technician: Technician | undefined;
}) {
  return (
    <div>
      <SummaryRow label="Vehicle" value={`${appointment.vehicle_year} ${appointment.vehicle_make} ${appointment.vehicle_model}`} />
      <SummaryRow label="Phone" value={appointment.customer_phone} />
      <SummaryRow label="Scheduled" value={formatDealershipWallClock(appointment.time_slot)} />
      <SummaryRow
        label="Technician"
        value={technician ? `${technician.name} (${SPECIALTY_LABELS[technician.specialty]})` : `#${appointment.technician_id}`}
      />
      <SummaryRow
        label="Urgency"
        value={
          appointment.urgency === "urgent" ? (
            <span className="rounded bg-red-900/60 px-2 py-0.5 text-xs text-red-300">Urgent</span>
          ) : (
            "Non-urgent"
          )
        }
      />
      <SummaryRow label="Status" value={STATUS_LABELS[appointment.status] ?? appointment.status} />
      <SummaryRow label="Appointment #" value={<span className="font-mono">{appointment.id}</span>} />
      <p className="mt-3 text-xs text-neutral-500">Confirmation SMS queued to this number.</p>
    </div>
  );
}

// One line per tool call, from code-recorded results - what the agent
// actually tried before handing off.
function describeToolResult({ tool, args, result }: HandoffSummaryData["tool_results"][number]): string {
  if ("error" in result) return `${tool}: ${String(result.error)}`;
  if (tool === "check_availability") {
    const slots = (result.slots as unknown[] | undefined) ?? [];
    return `Checked ${String(args.urgency).replace("_", "-")} availability: ${slots.length} slot(s)`;
  }
  if (tool === "find_appointment") {
    if (result.verified === false) return "Appointment lookup: caller not verified";
    return `Appointment lookup: ${((result.appointments as unknown[]) ?? []).length} found`;
  }
  if (tool === "book_appointment") return "Booked an appointment";
  if (tool === "cancel_appointment") return "Cancelled an appointment";
  return tool;
}

function HandoffSummary({ handoff }: { handoff: Handoff }) {
  const s = handoff.summary;
  return (
    <div>
      <SummaryRow label="Reason" value={REASON_LABELS[handoff.reason] ?? handoff.reason} />
      {s && (
        <SummaryRow
          label="Identity"
          value={
            s.verification.caller_id_verified ? (
              <span className="rounded bg-emerald-900/60 px-2 py-0.5 text-xs text-emerald-300">
                {s.verification.method === "sms_otp" ? "Verified by text code" : "Verified by Caller ID"}
              </span>
            ) : (
              <span className="rounded bg-amber-900/60 px-2 py-0.5 text-xs text-amber-300">Not verified</span>
            )
          }
        />
      )}
      {s?.caller_stated.vehicle && <SummaryRow label="Vehicle (caller said)" value={s.caller_stated.vehicle} />}
      {s?.caller_stated.symptom && <SummaryRow label="Symptom (caller said)" value={s.caller_stated.symptom} />}
      <SummaryRow label="Time" value={formatUtcAsDealershipTime(handoff.created_at)} />
      <SummaryRow label="Handoff #" value={<span className="font-mono">{handoff.id}</span>} />
      {s?.summary && (
        <div className="mt-4">
          <p className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Agent summary</p>
          <p className="text-sm text-neutral-100">{s.summary}</p>
        </div>
      )}
      {s && s.tool_results.length > 0 && (
        <div className="mt-4">
          <p className="mb-1 text-xs uppercase tracking-wide text-neutral-500">What the agent tried</p>
          <ul className="list-disc pl-5 text-sm text-neutral-300">
            {s.tool_results.map((t, i) => (
              <li key={i}>{describeToolResult(t)}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="mt-3 text-xs text-neutral-500">
        {handoff.customer_phone
          ? "Handoff SMS queued to this number - no real transfer occurred (simulated)."
          : "No verified number, so no SMS was queued - no real transfer occurred (simulated)."}
      </p>
    </div>
  );
}
