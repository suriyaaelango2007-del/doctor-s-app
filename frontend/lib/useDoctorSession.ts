"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { supabase, supabaseConfigError } from "./supabase";

/** Returns the doctor's Supabase access token, redirecting to /doctor/login when signed out. */
export function useDoctorSession(): { token: string | null; error: string | null } {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    if (supabaseConfigError) return;
    const sb = supabase();
    sb.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/doctor/login");
      else setToken(data.session.access_token);
    });
    const { data: sub } = sb.auth.onAuthStateChange((_event, session) => {
      if (!session) router.replace("/doctor/login");
      else setToken(session.access_token);
    });
    return () => sub.subscription.unsubscribe();
  }, [router]);

  return { token, error: supabaseConfigError };
}

export async function signOut() {
  await supabase().auth.signOut();
}
