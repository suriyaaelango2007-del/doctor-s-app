"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, doctorApi, type AppointmentRow, type Me } from "@/lib/api";
import { formatDate, formatPhone, formatTime, LANGUAGES } from "@/lib/format";
import { signOut, useDoctorSession } from "@/lib/useDoctorSession";
import { CallBadge, ComplianceBadge, StatusBadge } from "./components";

const POLL_MS = 30_000;

interface DashboardData {
  me: Me;
  tomorrow: AppointmentRow[];
  today: AppointmentRow[];
  /** server clock minus browser clock, so the countdown follows IST server time */
  clockOffset: number;
}

async function fetchDashboard(token: string): Promise<DashboardData> {
  const api = doctorApi(token);
  const me = await api.me();
  const clockOffset = Date.parse(me.now) - Date.now();
  const [tomorrow, today] = await Promise.all([
    api.appointments({ date: me.tomorrow }),
    api.appointments({ date: me.today, status: "CONFIRMED" }),
  ]);
  return { me, tomorrow, today, clockOffset };
}

export default function DoctorDashboard() {
  const router = useRouter();
  const { token, error: sessionError } = useDoctorSession();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!token) return;
    fetchDashboard(token).then(
      (d) => {
        setData(d);
        setError(null);
      },
      async (e) => {
        if (e instanceof ApiError && e.status === 401) {
          await signOut();
          router.replace("/doctor/login");
          return;
        }
        setError(e instanceof ApiError ? e.message : "Couldn't load appointments.");
      },
    );
  }, [token, router]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  const pending = useMemo(() => data?.tomorrow.filter((a) => a.status === "PENDING") ?? [], [data]);
  const confirmed = useMemo(() => data?.tomorrow.filter((a) => a.status === "CONFIRMED") ?? [], [data]);
  const closed = useMemo(
    () => data?.tomorrow.filter((a) => a.status === "REJECTED" || a.status === "AUTO_CANCELLED") ?? [],
    [data],
  );

  if (sessionError) return <Shell><p className="text-danger">{sessionError}</p></Shell>;
  if (!data) {
    return (
      <Shell>
        {error ? <ErrorBox message={error} onRetry={load} /> : <p className="text-muted">Loading…</p>}
      </Shell>
    );
  }

  return (
    <Shell clinic={data.me.clinic_name}>
      {error && <ErrorBox message={error} onRetry={load} />}

      <Section
        title="Pending"
        subtitle={`Tomorrow, ${formatDate(data.me.tomorrow)}`}
        right={<Countdown deadline={data.me.decision_deadline} offset={data.clockOffset} />}
      >
        {pending.length === 0 ? (
          <Empty>No bookings waiting for you.</Empty>
        ) : (
          pending.map((a) => <PendingCard key={a.id} appt={a} token={token!} onDone={load} />)
        )}
      </Section>

      <Section title="Tomorrow's confirmed" subtitle={`${confirmed.length} appointment${confirmed.length === 1 ? "" : "s"}`}>
        {confirmed.length === 0 ? (
          <Empty>Nothing confirmed yet.</Empty>
        ) : (
          confirmed.map((a) => (
            <Row key={a.id} appt={a}>
              <CallBadge status={a.call_status} />
            </Row>
          ))
        )}
        {closed.length > 0 && (
          <details className="mt-2 text-sm text-muted">
            <summary className="cursor-pointer select-none">{closed.length} rejected or cancelled</summary>
            <div className="mt-2 space-y-2">
              {closed.map((a) => (
                <Row key={a.id} appt={a}>
                  <StatusBadge status={a.status} />
                </Row>
              ))}
            </div>
          </details>
        )}
      </Section>

      <Section title="Today's appointments" subtitle={formatDate(data.me.today)}>
        {data.today.length === 0 ? (
          <Empty>No appointments today.</Empty>
        ) : (
          data.today.map((a) => (
            <Row key={a.id} appt={a} preview>
              {a.compliance_flag ? <ComplianceBadge /> : <CallBadge status={a.call_status} />}
            </Row>
          ))
        )}
      </Section>
    </Shell>
  );
}

function Shell({ children, clinic }: { children: React.ReactNode; clinic?: string }) {
  const router = useRouter();
  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-6 sm:px-6">
      <header className="mb-6 flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Dashboard</p>
          <p className="text-lg font-semibold">{clinic ?? " "}</p>
        </div>
        <button
          className="text-sm text-muted hover:text-ink"
          onClick={async () => {
            await signOut();
            router.replace("/doctor/login");
          }}
        >
          Sign out
        </button>
      </header>
      <div className="space-y-8">{children}</div>
    </main>
  );
}

function Section({
  title,
  subtitle,
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
        </div>
        {right}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>;
}

function ErrorBox({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-lg bg-danger-soft p-3 text-sm text-danger">
      {message}{" "}
      <button onClick={onRetry} className="font-semibold underline">
        Retry
      </button>
    </div>
  );
}

function Row({ appt, preview, children }: { appt: AppointmentRow; preview?: boolean; children?: React.ReactNode }) {
  return (
    <Link
      href={`/doctor/appointments/${appt.id}`}
      className="flex items-center gap-4 rounded-xl border border-line bg-card px-4 py-3 transition hover:border-accent/40"
    >
      <span className="w-18 shrink-0 font-semibold tabular-nums">{formatTime(appt.start_time)}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{appt.patient_name}</span>
        {preview && (
          <span className="block truncate text-sm text-muted">{appt.summary_preview ?? "No summary yet"}</span>
        )}
      </span>
      {children}
    </Link>
  );
}

function PendingCard({ appt, token, onDone }: { appt: AppointmentRow; token: string; onDone: () => void }) {
  const [busy, setBusy] = useState<"confirm" | "reject" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "confirm" | "reject") {
    setBusy(kind);
    setError(null);
    try {
      const api = doctorApi(token);
      if (kind === "confirm") await api.confirm(appt.id);
      else await api.reject(appt.id, reason.trim());
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
      setBusy(null);
      if (e instanceof ApiError && (e.status === 409 || e.status === 422)) onDone();
    }
  }

  return (
    <div className="rounded-xl border border-line bg-card p-4">
      <div className="flex items-start gap-4">
        <span className="w-18 shrink-0 text-lg font-semibold tabular-nums">{formatTime(appt.start_time)}</span>
        <div className="min-w-0 flex-1">
          <Link href={`/doctor/appointments/${appt.id}`} className="block truncate font-medium hover:underline">
            {appt.patient_name}
          </Link>
          <p className="text-sm text-muted">
            <a href={`tel:${appt.patient_phone}`} className="hover:underline">
              {formatPhone(appt.patient_phone)}
            </a>{" "}
            · {LANGUAGES[appt.preferred_language]}
          </p>
        </div>
      </div>

      {rejecting ? (
        <div className="mt-4 space-y-2">
          <input
            className="field text-sm"
            placeholder="Reason (optional, sent to patient)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
            autoFocus
          />
          <div className="flex gap-2">
            <button className="btn-danger flex-1" disabled={busy !== null} onClick={() => act("reject")}>
              {busy === "reject" ? "Rejecting…" : "Reject booking"}
            </button>
            <button className="btn-secondary" disabled={busy !== null} onClick={() => setRejecting(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex gap-2">
          <button className="btn-primary flex-1 py-3" disabled={busy !== null} onClick={() => act("confirm")}>
            {busy === "confirm" ? "Confirming…" : "Confirm"}
          </button>
          <button className="btn-secondary px-5 py-3" disabled={busy !== null} onClick={() => setRejecting(true)}>
            Reject
          </button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}

function Countdown({ deadline, offset }: { deadline: string; offset: number }) {
  const [now, setNow] = useState(() => Date.now() + offset);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() + offset), 1000);
    return () => clearInterval(id);
  }, [offset]);

  const ms = Date.parse(deadline) - now;
  if (ms <= 0) {
    return <span className="text-sm font-medium text-muted">Decision window closed</span>;
  }
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const urgent = ms < 30 * 60_000;
  return (
    <span
      className={`rounded-full px-3 py-1 text-sm font-medium tabular-nums ${
        urgent ? "bg-danger-soft text-danger" : "bg-warn-soft text-warn"
      }`}
      title="Unconfirmed bookings are cancelled automatically at 7:00 PM"
    >
      {h > 0 ? `${h}h ${m}m` : `${m}m ${String(s).padStart(2, "0")}s`} to 7 PM
    </span>
  );
}
