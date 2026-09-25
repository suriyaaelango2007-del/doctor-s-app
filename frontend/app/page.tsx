import Link from "next/link";
import { publicApi, type ClinicInfo } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  let clinic: ClinicInfo | null = null;
  try {
    clinic = await publicApi.clinic();
  } catch {
    // Backend unreachable — still render a usable page.
  }

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-5 py-16">
      <p className="text-sm font-medium tracking-wide text-accent uppercase">
        {clinic?.specialty ?? "Clinic"}
      </p>
      <h1 className="mt-2 text-4xl font-semibold tracking-tight text-balance">
        {clinic?.clinic_name ?? "Book your visit"}
      </h1>
      {clinic && <p className="mt-2 text-lg text-muted">with {clinic.doctor_name}</p>}

      <ol className="mt-10 space-y-4 text-[15px]">
        {[
          ["Pick a time for tomorrow", "Bookings for tomorrow are open until 6:00 PM today."],
          ["The doctor confirms", "You'll get an email by 7:00 PM."],
          ["A short call before your visit", "Our AI assistant calls to note your problem, so the doctor is ready for you."],
        ].map(([title, body], i) => (
          <li key={title} className="flex gap-4">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
              {i + 1}
            </span>
            <div>
              <p className="font-medium">{title}</p>
              <p className="text-muted">{body}</p>
            </div>
          </li>
        ))}
      </ol>

      <Link href="/book" className="btn-primary mt-10 py-3 text-base">
        Book an appointment
      </Link>
      <Link href="/doctor" className="mt-6 text-center text-sm text-muted underline-offset-4 hover:underline">
        Doctor login
      </Link>
    </main>
  );
}
