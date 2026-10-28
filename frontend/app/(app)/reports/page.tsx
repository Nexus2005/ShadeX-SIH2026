"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { API_BASE, apiGet, useAuth } from "@/lib/auth";
import type { ReportRecord } from "@/lib/types";
import { Button, Empty, ErrorBox, Spinner } from "@/components/ui";
import { fmtBytes, fmtDate } from "@/lib/format";

export default function ReportsPage() {
  const { token } = useAuth();
  const [reports, setReports] = useState<ReportRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const appTitles = useState<Record<string, { reference_no: string; title: string }>>({});

  const [apps, setApps] = useState(appTitles[0]);

  const load = useCallback(async () => {
    try {
      const d = await apiGet<{ reports: ReportRecord[] }>(token, "/reports");
      setReports(d.reports);
      const names: Record<string, { reference_no: string; title: string }> = {};
      const list = await apiGet<{ applications: { id: string; reference_no: string; title: string }[] }>(
        token, "/applications",
      ).catch(() => null);
      list?.applications.forEach((a) => {
        names[a.id] = { reference_no: a.reference_no, title: a.title };
      });
      setApps(names);
    } catch (e) {
      setError(String(e));
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function download(id: string) {
    try {
      const res = await fetch(`${API_BASE}/reports/${id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `scrutiny-report-${id.slice(0, 8)}.pdf`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Scrutiny reports</h1>
        <p className="text-sm text-slate-500">
          Signed PDF dossiers with executive summary, findings, evidence and verification outcomes.
        </p>
      </div>
      <ErrorBox message={error} />
      {!reports ? (
        <Spinner />
      ) : reports.length === 0 ? (
        <Empty>No reports generated yet — generate one from an application page.</Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2">Report code</th>
                <th className="px-4 py-2">Application</th>
                <th className="px-4 py-2">Generated</th>
                <th className="px-4 py-2">Size</th>
                <th className="px-4 py-2">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {reports.map((r) => (
                <tr key={r.id}>
                  <td className="px-4 py-2 font-mono text-xs">{r.reference_code}</td>
                  <td className="px-4 py-2">
                    <Link href={`/applications/${r.application_id}`} className="text-indigo-600 hover:underline">
                      {apps?.[r.application_id]?.reference_no ?? "—"}
                    </Link>
                    <span className="ml-2 text-slate-500">{apps?.[r.application_id]?.title}</span>
                  </td>
                  <td className="px-4 py-2 text-slate-600">{fmtDate(r.generated_at)}</td>
                  <td className="px-4 py-2 text-slate-600">{fmtBytes(r.size_bytes)}</td>
                  <td className="px-4 py-2">
                    <Button variant="secondary" onClick={() => void download(r.id)}>
                      Download PDF
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
