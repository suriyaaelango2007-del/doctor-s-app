"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, doctorApi, type AppointmentDetail } from "@/lib/api";
import { formatDate, formatDateTime, formatPhone, formatTime, LANGUAGES } from "@/lib/format";
import { signOut, useDoctorSession } from "@/lib/useDoctorSession";
import { CallBadge, StatusBadge } from "../../components";

export default function AppointmentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { token } = useDoctorSession();
  const [appt, setAppt] = useState<AppointmentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!token) return;
    doctorApi(token)
      .appointment(id)
      .then(
        (a) => {
          setAppt(a);
          setError(null);
        },
        async (e) => {
          if (e instanceof ApiError && e.status === 401) {
            await signOut();
            router.replace("/doctor/login");
            return;
          }
          setError(e instanceof ApiError ? e.message : "Couldn't load appointment.");
        },
      );
  }, [token, id, router]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-6 sm:px-6">
      <Link href="/doctor" className="text-sm text-muted hover:text-ink">
        ← Dashboard
      </Link>

      {error && <p className="mt-6 rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}
      {!appt && !error && <p className="mt-6 text-muted">Loading…</p>}

      {appt && (
        <div className="mt-4 space-y-6">
          <header>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{appt.patient_name}</h1>
              <StatusBadge status={appt.status} />
            </div>
            <p className="mt-1 text-muted">
              {formatDate(appt.date)} · {formatTime(appt.start_time)}
            </p>
          </header>

          <Card title="Patient">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted">Phone</dt>
              <dd>
                <a href={`tel:${appt.patient_phone}`} className="hover:underline">
                  {formatPhone(appt.patient_phone)}
                </a>
              </dd>
              <dt className="text-muted">Email</dt>
              <dd className="break-all">{appt.patient_email}</dd>
              <dt className="text-muted">Language</dt>
              <dd>{LANGUAGES[appt.preferred_language]}</dd>
              <dt className="text-muted">Booked</dt>
              <dd>{formatDateTime(appt.created_at)}</dd>
              {appt.decided_at && (
                <>
                  <dt className="text-muted">Decided</dt>
                  <dd>{formatDateTime(appt.decided_at)}</dd>
                </>
              )}
              {appt.reject_reason && (
                <>
                  <dt className="text-muted">Reason</dt>
                  <dd>{appt.reject_reason}</dd>
                </>
              )}
            </dl>
          </Card>

          <Card title="Summary">
            {appt.summary ? (
              <>
                {appt.summary.compliance_flag && (
                  <div className="mb-3 rounded-lg bg-danger-soft p-3 text-sm text-danger">
                    <p className="font-semibold">The AI assistant may have given advice on this call.</p>
                    {appt.summary.compliance_notes && <p className="mt-1">{appt.summary.compliance_notes}</p>}
                  </div>
                )}
                <pre className="overflow-x-auto text-sm whitespace-pre-wrap">
                  {JSON.stringify(appt.summary.summary, null, 2)}
                </pre>
              </>
            ) : (
              <p className="text-sm text-muted">
                {appt.status === "CONFIRMED"
                  ? "The summary will appear here after the patient's intake call."
                  : "No summary — this appointment wasn't confirmed."}
              </p>
            )}
          </Card>

          <Card title="Intake calls">
            {appt.calls.length === 0 ? (
              <p className="text-sm text-muted">No calls.</p>
            ) : (
              <ul className="divide-y divide-line text-sm">
                {appt.calls.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-3 py-2">
                    <span>
                      Attempt {c.attempt || "—"}
                      {c.started_at && <span className="text-muted"> · {formatDateTime(c.started_at)}</span>}
                      {c.duration_seconds != null && (
                        <span className="text-muted"> · {Math.round(c.duration_seconds / 60)} min</span>
                      )}
                      {c.failure_reason && <span className="block text-muted">{c.failure_reason}</span>}
                    </span>
                    <CallBadge status={c.status} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </main>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4">
      <h2 className="mb-3 text-sm font-semibold tracking-wide text-muted uppercase">{title}</h2>
      {children}
    </section>
  );
}
