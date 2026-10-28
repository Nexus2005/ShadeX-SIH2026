"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiGet, useAuth } from "@/lib/auth";
import type { Summary } from "@/lib/types";
import { Badge, Card, Empty, ErrorBox, Spinner, Stat } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

export default function DashboardPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Summary>(token, "/dashboard/summary")
      .then(setSummary)
      .catch((e) => setError(String(e)));
  }, [token]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Dashboard</h1>
        <p className="text-sm text-slate-500">
          Portfolio health of AICTE EoA applications under scrutiny.
        </p>
      </div>
      <ErrorBox message={error} />
      {!summary && !error ? (
        <Spinner label="Loading summary…" />
      ) : summary ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat
              label="Applications"
              value={summary.applications_total}
              sub={statusLine(summary.applications_by_status)}
            />
            <Stat
              label="Documents"
              value={summary.documents_total}
              sub={`${summary.documents_processed} processed · ${summary.documents_unreadable} unreadable`}
            />
            <Stat
              label="Open findings"
              value={summary.findings_open}
              sub={severityLine(summary.open_findings_by_severity)}
            />
            <Stat
              label="Recent validation runs"
              value={summary.recent_validation_runs?.length ?? 0}
              sub={summary.recent_validation_runs?.length ? "latest activity below" : "—"}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Open findings by severity">
              <Distribution data={summary.open_findings_by_severity} />
            </Card>
            <Card title="Applications by status">
              <Badges data={summary.applications_by_status} />
            </Card>
            <Card title="Applications by priority">
              <Badges data={summary.applications_by_priority} />
            </Card>
            <Card title="Findings by status">
              <Badges data={summary.findings_by_status} />
            </Card>
            <Card title="Recent validation runs" className="lg:col-span-2">
              {!summary.recent_validation_runs?.length ? (
                <Empty>No validation runs yet.</Empty>
              ) : (
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">Run</th>
                      <th className="py-2 pr-3">Application</th>
                      <th className="py-2 pr-3">Status</th>
                      <th className="py-2 pr-3">Started</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {summary.recent_validation_runs.map((r) => (
                      <tr key={r.id}>
                        <td className="py-2 pr-3 font-mono text-xs">{r.id}</td>
                        <td className="py-2 pr-3">
                          <Link
                            href={`/applications/${r.application_id}`}
                            className="text-indigo-600 hover:underline"
                          >
                            {r.application_id.slice(0, 8)}…
                          </Link>
                        </td>
                        <td className="py-2 pr-3"><Badge>{r.status}</Badge></td>
                        <td className="py-2 pr-3 text-slate-600">{fmtDate(r.started_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>

          <Card title="Quick access">
            <div className="flex flex-wrap gap-2">
              <Link href="/applications" className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
                Applications
              </Link>
              <Link href="/findings" className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
                Findings queue
              </Link>
              <Link href="/verification" className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
                Verification
              </Link>
              <Link href="/audit" className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
                Audit trail
              </Link>
            </div>
          </Card>
        </>
      ) : (
        <Empty>Nothing to show.</Empty>
      )}
    </div>
  );
}

function statusLine(byStatus: Record<string, number> | undefined): string {
  if (!byStatus) return "—";
  return Object.entries(byStatus).map(([k, v]) => `${titleCase(k)}: ${v}`).join(" · ") || "none";
}

function severityLine(bySev: Record<string, number> | undefined): string {
  if (!bySev) return "—";
  return ["critical", "high", "medium", "low", "info"]
    .filter((s) => bySev[s])
    .map((s) => `${s}: ${bySev[s]}`)
    .join(" · ") || "none";
}

function Distribution({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  if (!entries.length) return <Empty>No data yet.</Empty>;
  return (
    <div className="space-y-2">
      {entries
        .sort((a, b) => b[1] - a[1])
        .map(([key, count]) => (
          <div key={key} className="flex items-center gap-3">
            <div className="w-44 shrink-0">
              <Badge kind="severity">{key}</Badge>
            </div>
            <div className="min-w-0 flex-1">
              <div className="h-2 rounded bg-slate-100">
                <div
                  className="h-2 rounded bg-indigo-500"
                  style={{ width: `${total ? (count / total) * 100 : 0}%` }}
                />
              </div>
            </div>
            <div className="w-8 text-right text-sm font-medium text-slate-900">{count}</div>
          </div>
        ))}
    </div>
  );
}

function Badges({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  if (!entries.length) return <Empty>No data yet.</Empty>;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([key, count]) => (
        <span key={key} className="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-sm">
          <Badge>{key}</Badge>
          <strong className="text-slate-900">{count}</strong>
        </span>
      ))}
    </div>
  );
}
