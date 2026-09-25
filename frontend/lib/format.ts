import type { Language } from "./api";

/** "2026-09-24" -> "Thursday, 24 September". Parsed as a calendar date, no timezone shift. */
export function formatDate(iso: string, opts: Intl.DateTimeFormatOptions = {}): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
    ...opts,
  });
}

/** "10:30:00" -> "10:30 AM" */
export function formatTime(hms: string): string {
  const [h, m] = hms.split(":").map(Number);
  const suffix = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** ISO timestamp -> "24 Sep, 6:42 PM" in IST */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "Asia/Kolkata",
  });
}

export const LANGUAGES: Record<Language, string> = {
  ta: "தமிழ் (Tamil)",
  en: "English",
  hi: "हिन्दी (Hindi)",
};

export function formatPhone(e164: string): string {
  const m = e164.match(/^\+91(\d{5})(\d{5})$/);
  return m ? `+91 ${m[1]} ${m[2]}` : e164;
}
