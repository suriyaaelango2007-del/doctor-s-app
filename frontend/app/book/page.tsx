"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, publicApi, type ClinicInfo, type Language, type Slot, type SlotsResponse } from "@/lib/api";
import { formatDate, formatTime, LANGUAGES } from "@/lib/format";

const CONSENT_TEXT =
  "I agree to receive a call from the clinic's AI assistant, which will ask about my health problem and record the call for the doctor.";

export default function BookPage() {
  const router = useRouter();
  const [data, setData] = useState<SlotsResponse | null>(null);
  const [clinic, setClinic] = useState<ClinicInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [slot, setSlot] = useState<Slot | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([publicApi.slots(), publicApi.clinic().catch(() => null)]).then(
      ([slots, info]) => {
        setLoadError(null);
        setData(slots);
        setClinic(info);
        setSlot((current) => (current && slots.slots.some((s) => s.id === current.id) ? current : null));
      },
      (e) => setLoadError(e instanceof ApiError ? e.message : "Couldn't load available times."),
    );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const phoneDigits = phone.replace(/\D/g, "");
  const phoneValid = /^[6-9]\d{9}$/.test(phoneDigits);
  const canSubmit = slot && name.trim().length >= 2 && phoneValid && email.includes("@") && consent && !submitting;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!slot || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await publicApi.book({
        slot_id: slot.id,
        name: name.trim(),
        phone: `+91${phoneDigits}`,
        email: email.trim(),
        preferred_language: language,
        consent_ai_call: consent,
      });
      const qs = new URLSearchParams({ date: res.date, time: res.start_time, email: email.trim() });
      router.push(`/book/success?${qs}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      if (e instanceof ApiError && e.status === 409) {
        setSlot(null);
        load();
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-xl px-5 py-10">
      <Link href="/" className="text-sm text-muted hover:text-ink">
        ← {clinic?.clinic_name ?? "Back"}
      </Link>
      <h1 className="mt-4 text-3xl font-semibold tracking-tight">Book an appointment</h1>
      {clinic && <p className="mt-1 text-muted">with {clinic.doctor_name}</p>}

      {loadError && (
        <div className="mt-8 rounded-lg bg-danger-soft p-4 text-danger">
          {loadError}{" "}
          <button onClick={load} className="font-semibold underline">
            Retry
          </button>
        </div>
      )}

      {!data && !loadError && <p className="mt-8 text-muted">Loading available times…</p>}

      {data && !data.booking_open && (
        <div className="mt-8 rounded-xl border border-line bg-card p-6">
          <p className="text-lg font-semibold">Booking closed for tomorrow</p>
          <p className="mt-1 text-muted">
            Bookings for the next day open at midnight and close at 6:00 PM. Please come back after midnight.
          </p>
        </div>
      )}

      {data && data.booking_open && (
        <form onSubmit={submit} className="mt-8 space-y-8">
          <section>
            <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
              Tomorrow, {formatDate(data.date)}
            </h2>
            {data.slots.length === 0 ? (
              <p className="mt-3 rounded-lg border border-line bg-card p-4 text-muted">
                All of tomorrow&apos;s times are taken. Please try again after midnight for the next day.
              </p>
            ) : (
              <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
                {data.slots.map((s) => {
                  const selected = slot?.id === s.id;
                  return (
                    <button
                      type="button"
                      key={s.id}
                      onClick={() => setSlot(s)}
                      aria-pressed={selected}
                      className={`rounded-lg border px-2 py-3 text-sm font-medium tabular-nums transition ${
                        selected
                          ? "border-accent bg-accent text-white"
                          : "border-line bg-card hover:border-accent/50"
                      }`}
                    >
                      {formatTime(s.start_time)}
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          {data.slots.length > 0 && (
            <section className="space-y-4">
              <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">Your details</h2>

              <label className="block">
                <span className="mb-1.5 block text-sm font-medium">Full name</span>
                <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
              </label>

              <label className="block">
                <span className="mb-1.5 block text-sm font-medium">Mobile number</span>
                <div className="flex">
                  <span className="flex items-center rounded-l-lg border border-r-0 border-line bg-paper px-3 text-muted">
                    +91
                  </span>
                  <input
                    className="field rounded-l-none"
                    inputMode="numeric"
                    autoComplete="tel-national"
                    placeholder="98765 43210"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value.replace(/[^\d ]/g, "").slice(0, 11))}
                    required
                  />
                </div>
                {phone && !phoneValid && (
                  <span className="mt-1 block text-sm text-danger">Enter a 10-digit mobile number</span>
                )}
                <span className="mt-1 block text-sm text-muted">Our assistant will call you on this number.</span>
              </label>

              <label className="block">
                <span className="mb-1.5 block text-sm font-medium">Email</span>
                <input
                  className="field"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
                <span className="mt-1 block text-sm text-muted">We&apos;ll email you when the doctor confirms.</span>
              </label>

              <fieldset>
                <legend className="mb-1.5 text-sm font-medium">Language for the call</legend>
                <div className="grid grid-cols-3 gap-2">
                  {(Object.keys(LANGUAGES) as Language[]).map((l) => (
                    <label
                      key={l}
                      className={`cursor-pointer rounded-lg border px-2 py-2.5 text-center text-sm transition ${
                        language === l ? "border-accent bg-accent-soft font-medium text-accent" : "border-line bg-card"
                      }`}
                    >
                      <input
                        type="radio"
                        name="language"
                        value={l}
                        checked={language === l}
                        onChange={() => setLanguage(l)}
                        className="sr-only"
                      />
                      {LANGUAGES[l]}
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="flex gap-3 rounded-lg border border-line bg-card p-4">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="mt-0.5 size-5 shrink-0 accent-accent"
                  required
                />
                <span className="text-sm leading-relaxed">{CONSENT_TEXT}</span>
              </label>
            </section>
          )}

          {error && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}

          {data.slots.length > 0 && (
            <div className="sticky bottom-0 -mx-5 border-t border-line bg-paper/95 px-5 py-4 backdrop-blur">
              <button type="submit" disabled={!canSubmit} className="btn-primary w-full py-3 text-base">
                {submitting
                  ? "Sending request…"
                  : slot
                    ? `Request ${formatTime(slot.start_time)} tomorrow`
                    : "Pick a time"}
              </button>
            </div>
          )}
        </form>
      )}
    </main>
  );
}
