const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Language = "ta" | "en" | "hi";
export type AppointmentStatus = "PENDING" | "CONFIRMED" | "REJECTED" | "AUTO_CANCELLED";
export type CallStatus =
  | "QUEUED"
  | "CALLING"
  | "COMPLETED"
  | "NO_ANSWER"
  | "FAILED"
  | "FORM_SENT"
  | "FORM_SUBMITTED";

export interface ClinicInfo {
  clinic_name: string;
  doctor_name: string;
  specialty: string;
}

export interface Slot {
  id: string;
  date: string; // YYYY-MM-DD
  start_time: string; // HH:MM:SS
  end_time: string;
}

export interface SlotsResponse {
  date: string;
  booking_open: boolean;
  slots: Slot[];
}

export interface BookingInput {
  slot_id: string;
  name: string;
  phone: string;
  email: string;
  preferred_language: Language;
  consent_ai_call: boolean;
}

export interface BookingResult {
  id: string;
  status: AppointmentStatus;
  date: string;
  start_time: string;
}

export interface Me {
  id: string;
  name: string;
  specialty: string;
  clinic_name: string;
  email: string;
  today: string;
  tomorrow: string;
  now: string;
  decision_deadline: string;
}

export interface AppointmentRow {
  id: string;
  status: AppointmentStatus;
  created_at: string;
  decided_at: string | null;
  reject_reason: string | null;
  slot_id: string;
  date: string;
  start_time: string;
  end_time: string;
  patient_name: string;
  patient_phone: string;
  preferred_language: Language;
  call_status: CallStatus | null;
  call_attempt: number | null;
  summary_preview: string | null;
  compliance_flag: boolean | null;
  hospital_advice_given: boolean | null;
}

export interface CallRow {
  id: string;
  status: CallStatus;
  attempt: number;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  transcript: unknown;
  failure_reason: string | null;
  next_retry_at: string | null;
  created_at: string;
}

export interface AppointmentDetail {
  id: string;
  status: AppointmentStatus;
  date: string;
  start_time: string;
  end_time: string;
  created_at: string;
  decided_at: string | null;
  reject_reason: string | null;
  consent_ai_call: boolean;
  consent_at: string;
  patient_name: string;
  patient_phone: string;
  patient_email: string;
  preferred_language: Language;
  decision_deadline: string;
  calls: CallRow[];
  summary: {
    source: "CALL" | "FORM";
    summary: IntakeSummary;
    compliance_flag: boolean;
    compliance_notes: string | null;
    created_at: string;
  } | null;
}

/** Summary JSON (spec §10). */
export interface IntakeSummary {
  chief_complaint: string;
  duration: string;
  severity: "mild" | "moderate" | "severe" | "not mentioned";
  symptoms: string[];
  current_medicines: string[];
  allergies: string[];
  past_treatments: string[];
  specialty_answers: Record<string, string>;
  patient_questions: string[];
  hospital_advice_given: boolean;
  language: Language;
  notes: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc, msg}]
    return detail
      .map((d: { loc?: string[]; msg?: string }) => {
        const field = d.loc?.[d.loc.length - 1];
        const msg = (d.msg ?? "is invalid").replace(/^Value error, /, "");
        return field && field !== "body" ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(body, res.status));
  return body as T;
}

export const publicApi = {
  clinic: () => request<ClinicInfo>("/api/clinic"),
  slots: () => request<SlotsResponse>("/api/slots"),
  book: (input: BookingInput) =>
    request<BookingResult>("/api/appointments", { method: "POST", body: JSON.stringify(input) }),
  form: (token: string) => request<FormInfo>(`/api/forms/${encodeURIComponent(token)}`),
  submitForm: (token: string, answers: FormAnswers) =>
    request<null>(`/api/forms/${encodeURIComponent(token)}`, { method: "POST", body: JSON.stringify(answers) }),
};

/** Fallback intake form (milestone 7). */
export interface FormInfo {
  patient_name: string;
  doctor_name: string;
  clinic_name: string;
  date: string;
  start_time: string;
  language: Language;
  specialty_questions: string[];
  expires_at: string;
}

export interface FormAnswers {
  language: Language;
  main_problem: string;
  duration: string;
  severity: "mild" | "moderate" | "severe";
  current_medicines: string;
  allergies: string;
  past_treatments: string;
  specialty_answers: Record<string, string>;
  patient_questions: string;
}

export function doctorApi(token: string) {
  return {
    me: () => request<Me>("/api/doctor/me", {}, token),
    appointments: (params: { date?: string; status?: AppointmentStatus } = {}) => {
      const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
      return request<AppointmentRow[]>(`/api/doctor/appointments?${qs}`, {}, token);
    },
    appointment: (id: string) => request<AppointmentDetail>(`/api/doctor/appointments/${id}`, {}, token),
    confirm: (id: string) =>
      request<{ id: string; status: string }>(`/api/doctor/appointments/${id}/confirm`, { method: "POST" }, token),
    reject: (id: string, reason?: string) =>
      request<{ id: string; status: string }>(
        `/api/doctor/appointments/${id}/reject`,
        { method: "POST", body: JSON.stringify({ reason: reason || null }) },
        token,
      ),
  };
}
