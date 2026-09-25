"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { supabase, supabaseConfigError } from "@/lib/supabase";

export default function DoctorLogin() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(supabaseConfigError);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (supabaseConfigError) return;
    supabase()
      .auth.getSession()
      .then(({ data }) => {
        if (data.session) router.replace("/doctor");
      });
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { error } = await supabase().auth.signInWithPassword({ email: email.trim(), password });
      if (error) throw error;
      router.replace("/doctor");
    } catch (e) {
      setError((e as Error).message || "Sign in failed");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-5 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Doctor sign in</h1>
      <form onSubmit={submit} className="mt-8 space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium">Email</span>
          <input
            className="field"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium">Password</span>
          <input
            className="field"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary w-full py-3">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
