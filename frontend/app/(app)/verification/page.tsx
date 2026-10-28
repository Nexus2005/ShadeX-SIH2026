"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { VerificationAttempt, VerificationProvider } from "@/lib/types";
import { Badge, Button, Card, Empty, ErrorBox, Spinner, Tabs } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

export default function VerificationPage() {
  const { token, me } = useAuth();
  const [tab, setTab] = useState("providers");
  const [providers, setProviders] = useState<VerificationProvider[] | null>(null);
  const [attempts, setAttempts] = useState<VerificationAttempt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const isAdmin = me?.role === "admin";

  const load = useCallback(() => {
    apiGet<{ providers: VerificationProvider[] }>(token, "/verification/providers")
      .then((d) => setProviders(d.providers))
      .catch((e) => setError(String(e)));
    apiGet<{ attempts: VerificationAttempt[] }>(token, "/verification")
      .then((d) => setAttempts(d.attempts))
      .catch(() => setAttempts(null));
  }, [token]);

  useEffect(load, [load]);

  async function toggleAvailability(code: string, availability: string) {
    setBusy(code);
    setError(null);
    try {
      await apiSend(token ?? "", `/verification/providers/${code}/availability`, "POST", { availability });
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Verification</h1>
        <p className="text-sm text-slate-500">
          All issuers&apos; adapters are simulated &amp; labelled — no live gov integration is authorized.
        </p>
      </div>
      <ErrorBox message={error} />
      <Tabs
        tabs={[
          { key: "providers", label: "Providers" },
          { key: "attempts", label: "Attempt log", badge: attempts?.length },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "providers" &&
        (!providers ? (
          <Spinner />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {providers.map((p) => (
              <Card key={p.code} title={p.name}>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge kind="verification">{p.availability}</Badge>
                  <Badge>{p.type}</Badge>
                  <span className="font-mono text-xs text-slate-400">{p.code}</span>
                </div>
                {p.description && <p className="mt-2 text-sm text-slate-600">{p.description}</p>}
                <div className="mt-2 text-xs text-slate-500">
                  Supports: {p.supported_document_categories.map((c) => titleCase(c)).join(", ") || "—"}
                </div>
                {isAdmin && (
                  <div className="mt-3 flex gap-2">
                    {["available", "unavailable"].map((a) => (
                      <Button
                        key={a}
                        variant={p.availability === a ? "primary" : "secondary"}
                        disabled={busy === p.code || p.availability === a}
                        onClick={() => void toggleAvailability(p.code, a)}
                      >
                        Set {a}
                      </Button>
                    ))}
                  </div>
                )}
              </Card>
            ))}
          </div>
        ))}
      {tab === "attempts" &&
        (!attempts ? (
          attempts === null ? (
            <Empty>Attempt log requires staff access.</Empty>
          ) : (
            <Spinner />
          )
        ) : attempts.length === 0 ? (
          <Empty>No verification attempts yet.</Empty>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Provider</th>
                  <th className="px-4 py-2">Application</th>
                  <th className="px-4 py-2">Reference</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Requested</th>
                  <th className="px-4 py-2">Compared</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {attempts.map((a) => (
                  <tr key={a.id}>
                    <td className="px-4 py-2 font-medium">{a.provider_code}</td>
                    <td className="px-4 py-2 font-mono text-xs">
                      <span className="text-indigo-600">{a.application_id.slice(0, 8)}…</span>
                    </td>
                    <td className="px-4 py-2 text-slate-600">{a.reference_no ?? "—"}</td>
                    <td className="px-4 py-2"><Badge kind="verification">{a.status}</Badge></td>
                    <td className="px-4 py-2 text-slate-600">{fmtDate(a.requested_at)}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-600">
                      {Object.entries(a.compared_fields ?? {}).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
