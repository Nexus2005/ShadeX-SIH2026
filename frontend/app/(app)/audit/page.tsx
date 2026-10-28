"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { AuditEventRecord } from "@/lib/types";
import { Button, Card, Empty, ErrorBox, Spinner } from "@/components/ui";
import { fmtDate } from "@/lib/format";

export default function AuditPage() {
  const { token } = useAuth();
  const [events, setEvents] = useState<AuditEventRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<{ audit_id: string; problem: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionFilter, setActionFilter] = useState("");

  const load = useCallback(() => {
    apiGet<{ events: AuditEventRecord[] }>(token, "/audit")
      .then((d) => setEvents(d.events))
      .catch((e) => setError(String(e)));
  }, [token]);

  useEffect(load, [load]);

  async function verifyChain() {
    setBusy(true);
    setError(null);
    try {
      const d = await apiSend<{ problems: { audit_id: string; problem: string }[] }>(
        token ?? "", "/audit/verify-chain", "GET" as never,
      );
      setProblems(d.problems);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const filtered = events?.filter((e) =>
    actionFilter ? e.action.startsWith(actionFilter) : true,
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Audit trail</h1>
          <p className="text-sm text-slate-500">
            Append-only SHA-256 hash-chained events; each row links to the previous hash.
          </p>
        </div>
        <Button onClick={() => void verifyChain()} disabled={busy}>
          {busy ? "Verifying…" : "Verify chain integrity"}
        </Button>
      </div>
      <ErrorBox message={error} />
      {problems && (
        <Card title="Chain verification result">
          {problems.length === 0 ? (
            <div className="text-sm text-emerald-700">✔ No integrity problems detected — chain is valid.</div>
          ) : (
            <ul className="list-disc pl-5 text-sm text-rose-700">
              {problems.map((p) => (
                <li key={p.audit_id}>
                  event {p.audit_id}: {p.problem}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      <div className="flex gap-2">
        {["", "auth.", "application.", "document.", "finding.", "verification.", "rule.", "report.", "field."].map(
          (a) => (
            <button
              key={a}
              onClick={() => setActionFilter(a)}
              className={`rounded-full px-3 py-1 text-xs ${
                actionFilter === a ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50 border border-slate-300"
              }`}
            >
              {a || "all"}
            </button>
          ),
        )}
      </div>
      {!filtered ? (
        <Spinner />
      ) : filtered.length === 0 ? (
        <Empty>No audit events.</Empty>
      ) : (
        <div className="max-h-[65vh] space-y-2 overflow-y-auto pr-1">
          {filtered.map((e) => (
            <div key={e.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-white">{e.action}</span>
                  <span className="text-xs text-slate-500">
                    {e.entity_type}
                    {e.entity_id ? ` · ${e.entity_id.slice(0, 8)}…` : ""}
                  </span>
                </div>
                <span className="text-xs text-slate-400">
                  #{e.id} · {fmtDate(e.occurred_at)} · {e.actor_email ?? "system"}
                  {e.actor_role ? ` (${e.actor_role})` : ""}
                </span>
              </div>
              {e.meta && Object.keys(e.meta).length > 0 && (
                <pre className="mt-2 max-w-2xl overflow-auto rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-600">
                  {JSON.stringify(e.meta, null, 1)}
                </pre>
              )}
              <div className="mt-1 truncate font-mono text-[10px] text-slate-400" title={e.event_hash}>
                hash {e.event_hash.slice(0, 24)}… ✔ linked to previous
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
