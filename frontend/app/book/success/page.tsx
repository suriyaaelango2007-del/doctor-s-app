import Link from "next/link";
import { formatDate, formatTime } from "@/lib/format";

export default async function BookingSuccess({ searchParams }: PageProps<"/book/success">) {
  const { date, time, email } = await searchParams;
  const d = typeof date === "string" ? date : null;
  const t = typeof time === "string" ? time : null;

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-5 py-16">
      <div className="flex size-12 items-center justify-center rounded-full bg-accent-soft text-2xl text-accent">✓</div>
      <h1 className="mt-6 text-3xl font-semibold tracking-tight">Request sent</h1>
      {d && t && (
        <p className="mt-2 text-lg">
          {formatDate(d)} at {formatTime(t)}
        </p>
      )}
      <p className="mt-4 text-muted">
        You&apos;ll get an email{typeof email === "string" ? ` at ${email}` : ""} once the doctor confirms, usually by 7:00
        PM today.
      </p>
      <p className="mt-2 text-muted">
        After confirmation, our AI assistant will call you to note your problem for the doctor.
      </p>
      <Link href="/" className="btn-secondary mt-10 self-start">
        Done
      </Link>
    </main>
  );
}
