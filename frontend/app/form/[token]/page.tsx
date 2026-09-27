"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, publicApi, type FormAnswers, type FormInfo, type Language } from "@/lib/api";
import { LANGUAGES } from "@/lib/format";
import { FORM_TEXT, formatWhen, specialtyQuestion } from "@/lib/formText";

type Severity = FormAnswers["severity"];

export default function IntakeFormPage() {
  const { token } = useParams<{ token: string }>();
  const [info, setInfo] = useState<FormInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lang, setLang] = useState<Language>("en");

  const [mainProblem, setMainProblem] = useState("");
  const [duration, setDuration] = useState("");
  const [severity, setSeverity] = useState<Severity | null>(null);
  const [medicines, setMedicines] = useState("");
  const [allergies, setAllergies] = useState("");
  const [pastTreatments, setPastTreatments] = useState("");
  const [specialty, setSpecialty] = useState<Record<string, string>>({});
  const [questions, setQuestions] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    publicApi.form(token).then(
      (f) => {
        setInfo(f);
        setLang(f.language);
      },
      (e) =>
        setLoadError(
          e instanceof ApiError && e.status === 404
            ? "This form link is not valid. Please use the link from your email."
            : e instanceof ApiError
              ? e.message
              : "Couldn't load the form. Please try again.",
        ),
    );
  }, [token]);

  const t = FORM_TEXT[lang];

  if (loadError) {
    return (
      <Shell>
        <p className="rounded-xl border border-line bg-card p-5 text-lg">{loadError}</p>
      </Shell>
    );
  }
  if (!info) {
    return (
      <Shell>
        <p className="text-muted">Loading…</p>
      </Shell>
    );
  }
  if (done) {
    return (
      <Shell>
        <div className="flex size-12 items-center justify-center rounded-full bg-accent-soft text-2xl text-accent">✓</div>
        <h1 className="mt-6 text-3xl font-semibold tracking-tight">{t.doneTitle}</h1>
        <p className="mt-3 text-lg text-muted">{t.doneBody}</p>
      </Shell>
    );
  }

  const canSubmit = mainProblem.trim().length >= 2 && severity !== null && !submitting;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !severity) {
      setError(t.required);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await publicApi.submitForm(token, {
        language: lang,
        main_problem: mainProblem.trim(),
        duration: duration.trim(),
        severity,
        current_medicines: medicines.trim(),
        allergies: allergies.trim(),
        past_treatments: pastTreatments.trim(),
        specialty_answers: specialty,
        patient_questions: questions.trim(),
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <Shell>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium tracking-wide text-accent uppercase">{info.clinic_name}</p>
        <label className="flex items-center gap-2 text-sm text-muted">
          <span className="sr-only">{t.language}</span>
          <select
            value={lang}
            onChange={(e) => setLang(e.target.value as Language)}
            className="rounded-lg border border-line bg-card px-2 py-1.5 text-sm text-ink"
          >
            {(Object.keys(LANGUAGES) as Language[]).map((l) => (
              <option key={l} value={l}>
                {LANGUAGES[l]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <h1 className="mt-4 text-2xl leading-snug font-semibold tracking-tight">{t.title}</h1>
      <p className="mt-3">{t.hello(info.patient_name)}</p>
      <p className="mt-1 text-muted">{t.intro(info.doctor_name, formatWhen(info.date, info.start_time, lang))}</p>

      <form onSubmit={submit} className="mt-8 space-y-6" lang={lang}>
        <Field label={t.mainProblem}>
          <textarea
            className="field min-h-24"
            value={mainProblem}
            onChange={(e) => setMainProblem(e.target.value)}
            maxLength={2000}
            required
          />
        </Field>

        <Field label={t.duration}>
          <input
            className="field"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            placeholder={t.durationHint}
            maxLength={200}
          />
        </Field>

        <fieldset>
          <legend className="mb-2 font-medium">{t.severity}</legend>
          <div className="grid grid-cols-3 gap-2">
            {(["mild", "moderate", "severe"] as Severity[]).map((s) => (
              <label
                key={s}
                className={`cursor-pointer rounded-lg border px-2 py-3 text-center transition ${
                  severity === s ? "border-accent bg-accent-soft font-medium text-accent" : "border-line bg-card"
                }`}
              >
                <input
                  type="radio"
                  name="severity"
                  value={s}
                  checked={severity === s}
                  onChange={() => setSeverity(s)}
                  className="sr-only"
                />
                {t[s]}
              </label>
            ))}
          </div>
        </fieldset>

        <Field label={t.medicines} hint={t.listHint} optional={t.optional}>
          <input className="field" value={medicines} onChange={(e) => setMedicines(e.target.value)} maxLength={1000} />
        </Field>

        <Field label={t.allergies} hint={t.listHint} optional={t.optional}>
          <input className="field" value={allergies} onChange={(e) => setAllergies(e.target.value)} maxLength={1000} />
        </Field>

        <Field label={t.pastTreatments} optional={t.optional}>
          <input
            className="field"
            value={pastTreatments}
            onChange={(e) => setPastTreatments(e.target.value)}
            maxLength={1000}
          />
        </Field>

        {info.specialty_questions.length > 0 && (
          <section className="space-y-4 rounded-xl border border-line bg-card p-4">
            <h2 className="font-semibold">{t.more}</h2>
            {info.specialty_questions.map((q) => (
              <Field key={q} label={specialtyQuestion(q, lang)} optional={t.optional}>
                <input
                  className="field"
                  value={specialty[q] ?? ""}
                  onChange={(e) => setSpecialty((prev) => ({ ...prev, [q]: e.target.value }))}
                  maxLength={500}
                />
              </Field>
            ))}
          </section>
        )}

        <Field label={t.patientQuestions} hint={t.patientQuestionsHint} optional={t.optional}>
          <textarea
            className="field min-h-20"
            value={questions}
            onChange={(e) => setQuestions(e.target.value)}
            maxLength={2000}
          />
        </Field>

        <p className="rounded-lg bg-warn-soft p-3 text-sm text-warn">{t.safety}</p>

        {error && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}

        <button type="submit" disabled={submitting} className="btn-primary w-full py-3 text-base">
          {submitting ? t.sending : t.submit}
        </button>
      </form>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto w-full max-w-xl flex-1 px-5 py-10">{children}</main>;
}

function Field({
  label,
  hint,
  optional,
  children,
}: {
  label: string;
  hint?: string;
  optional?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block font-medium">
        {label}
        {optional && <span className="ml-1 text-sm font-normal text-muted">({optional})</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-sm text-muted">{hint}</span>}
    </label>
  );
}
