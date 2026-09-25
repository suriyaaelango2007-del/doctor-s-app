import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
// New projects use a publishable key (sb_publishable_...); older ones use the anon key.
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/** Non-null when the frontend isn't configured for Supabase login. */
export const supabaseConfigError =
  url && key ? null : "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY must be set in frontend/.env";

let client: SupabaseClient | null = null;

/** Browser-only Supabase client, used just for the doctor's login session. */
export function supabase(): SupabaseClient {
  if (supabaseConfigError) throw new Error(supabaseConfigError);
  client ??= createClient(url!, key!, { auth: { persistSession: true, autoRefreshToken: true } });
  return client;
}
