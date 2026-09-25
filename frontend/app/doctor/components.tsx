import type { AppointmentStatus, CallStatus } from "@/lib/api";

const CALL_BADGES: Record<CallStatus, [string, string]> = {
  QUEUED: ["Call queued", "bg-paper text-muted border-line"],
  CALLING: ["Calling", "bg-accent-soft text-accent border-accent/20"],
  COMPLETED: ["Call done", "bg-accent-soft text-accent border-accent/20"],
  NO_ANSWER: ["No answer", "bg-warn-soft text-warn border-warn/20"],
  FAILED: ["Call failed", "bg-warn-soft text-warn border-warn/20"],
  FORM_SENT: ["Form sent", "bg-warn-soft text-warn border-warn/20"],
  FORM_SUBMITTED: ["Form received", "bg-accent-soft text-accent border-accent/20"],
};

const STATUS_BADGES: Record<AppointmentStatus, [string, string]> = {
  PENDING: ["Pending", "bg-warn-soft text-warn border-warn/20"],
  CONFIRMED: ["Confirmed", "bg-accent-soft text-accent border-accent/20"],
  REJECTED: ["Rejected", "bg-paper text-muted border-line"],
  AUTO_CANCELLED: ["Auto-cancelled", "bg-paper text-muted border-line"],
};

function Badge({ label, cls }: { label: string; cls: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${cls}`}>
      {label}
    </span>
  );
}

export function CallBadge({ status }: { status: CallStatus | null }) {
  if (!status) return null;
  const [label, cls] = CALL_BADGES[status];
  return <Badge label={label} cls={cls} />;
}

export function StatusBadge({ status }: { status: AppointmentStatus }) {
  const [label, cls] = STATUS_BADGES[status];
  return <Badge label={label} cls={cls} />;
}

export function ComplianceBadge() {
  return <Badge label="⚠ Check AI call" cls="bg-danger-soft text-danger border-danger/20" />;
}
