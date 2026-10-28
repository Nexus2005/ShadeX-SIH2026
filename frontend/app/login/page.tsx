"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Button, ErrorBox, Field, inputClass } from "@/components/ui";

const DEMO_USERS = [
  { email: "admin@resilient-ai.local", label: "AICTE admin" },
  { email: "reviewer@resilient-ai.local", label: "Scrutiny reviewer" },
  { email: "institution@resilient-ai.local", label: "Institution head" },
  { email: "auditor@resilient-ai.local", label: "Auditor (read-only)" },
];

export default function LoginPage() {
  const { login, me, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("admin@resilient-ai.remote".replace("remote", "local"));
  const [password, setPassword] = useState("Password123!");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && me) router.replace("/dashboard");
  }, [loading, me, router]);

  async function doLogin(mail: string, pw: string) {
    setError(null);
    setBusy(true);
    try {
      await login(mail, pw);
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <div className="text-2xl font-bold text-white">Resilient AI</div>
          <div className="mt-1 text-sm text-slate-400">
            AICTE application validation & compliance platform
          </div>
        </div>
        <div className="rounded-lg bg-white p-6 shadow-lg">
          <ErrorBox message={error} />
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void doLogin(email, password);
            }}
          >
            <Field label="Email">
              <input
                className={inputClass}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
              />
            </Field>
            <Field label="Password">
              <input
                className={inputClass}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </Field>
            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
          <div className="mt-6 border-t border-slate-200 pt-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              Demo accounts (password: Password123!)
            </div>
            <div className="grid grid-cols-2 gap-2">
              {DEMO_USERS.map((u) => (
                <button
                  key={u.email}
                  onClick={() => void doLogin(u.email, "Password123!")}
                  className="rounded border border-slate-300 px-2 py-1.5 text-left text-xs text-slate-700 hover:border-indigo-400 hover:bg-indigo-50"
                  disabled={busy}
                >
                  <span className="block font-medium">{u.label}</span>
                  <span className="block text-[10px] text-slate-400">{u.email}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="mt-4 text-center text-xs text-slate-500">
          Demo environment — verification adapters are simulated and outcomes are deterministic.
        </p>
      </div>
    </div>
  );
}
