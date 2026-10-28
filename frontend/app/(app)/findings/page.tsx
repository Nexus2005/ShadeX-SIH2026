"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { Finding } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBox,
  Field,
  Spinner,
  inputClass,
} from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

export default function FindingsPage() {
  const { token, me } = useAuth();
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [selected, setSelected] = useState<Finding | null>(null);
  const [detail, setDetail] = useState<Finding | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const canReview = me?.role === "admin" || me?.role === "reviewer";

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (severity) params.set("severity", severity);
    apiGet<{ findings: Finding[] }>(token, `/findings?${params.toString()}`)
      .then((d) => setFindings(d.findings))
      .catch((e) => setError(String(e)));
  }, [token, status, severity]);

  useEffect(load, [load]);

  async function open(f: Finding) {
    setSelected(f);
    setDetail(f);
    try {
      const d = await apiGet<Finding>(token ?? "", `/findings/${f.id}`);
      setDetail(d);
    } catch {
      /* keep cached */
    }
  }

  async function review(action: string) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend(token ?? "", `/findings/${selected.id}/review`, "POST", {
        action,
        comment: comment || undefined,
      });
      setComment("");
      const d = await apiGet<Finding>(token ?? "", `/findings/${selected.id}`);
      setDetail(d);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Findings queue</h1>
        <p className="text-sm text-slate-500">
          Deterministic rule findings, consistency checks and simulated verification outcomes.
        </p>
      </div>
      <ErrorBox message={error} />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <div className="flex gap-2">
            <select className={`${inputClass} w-44`} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {["open", "under_review", "needs_clarification", "confirmed", "dismissed", "waived", "resolved"].map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </select>
            <select className={`${inputClass} w-44`} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">All severities</option>
              {["critical", "high", "medium", "low", "info"].map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </select>
          </div>
          {!findings ? (
            <Spinner />
          ) : findings.length === 0 ? (
            <Empty>No findings match the filter.</Empty>
          ) : (
            <ul className="max-h-[70vh] space-y-2 overflow-y-auto pr-1">
              {findings.map((f) => (
                <li key={f.id}>
                  <button
                    onClick={() => void open(f)}
                    className={`w-full rounded-lg border p-3 text-left text-sm shadow-sm transition ${
                      selected?.id === f.id
                        ? "border-indigo-400 bg-indigo-50"
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-slate-900">{f.title}</span>
                      <Badge kind="severity">{f.severity}</Badge>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <Badge>{f.status}</Badge>
                      {f.reference_no && (
                        <Link
                          href={`/applications/${f.application_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="text-indigo-600 hover:underline"
                        >
                          {f.reference_no}
                        </Link>
                      )}
                      <span>{f.category}</span>
                      {f.is_simulated && <span className="text-amber-600">simulated</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          {!detail ? (
            <Empty>Select a finding to review.</Empty>
          ) : (
            <Card title={detail.title}>
              <dl className="grid grid-cols-1 gap-x-6 text-sm sm:grid-cols-2">
                {(
                [
                  ["Code", detail.finding_code],
                  ["Status", <Badge key="s">{detail.status}</Badge>],
                  ["Severity", <Badge key="v" kind="severity">{detail.severity}</Badge>],
                  ["Confidence", detail.confidence != null ? `${(detail.confidence * 100).toFixed(0)}%` : "—"],
                  ["Expected", detail.expected_value],
                  ["Observed", detail.observed_value],
                  ["Last detected", fmtDate(detail.last_detected_at)],
                  ["Method", detail.detection_method],
                ] as [string, React.ReactNode][]
              ).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-b border-slate-100 py-1">
                    <dt className="text-slate-500">{k}</dt>
                    <dd className="text-right font-medium text-slate-900">{v ?? "—"}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-3 text-sm text-slate-700">{detail.description}</p>
              {detail.recommended_action && (
                <p className="mt-2 text-sm text-slate-600">
                  <span className="font-medium">Recommended:</span> {detail.recommended_action}
                </p>
              )}
              {!!detail.evidence?.length && (
                <div className="mt-4">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
                  <ul className="space-y-3">
                    {detail.evidence.map((ev) => (
                      <li key={ev.id} className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
                        {ev.excerpt && (
                          <blockquote className="border-l-2 border-indigo-300 pl-2 font-mono text-xs text-slate-700">
                            {ev.excerpt}
                          </blockquote>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {canReview && (
                <div className="mt-4 border-t border-slate-100 pt-3">
                  <Field label="Reviewer comment">
                    <textarea
                      className={inputClass}
                      rows={2}
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Optional justification recorded on the audit trail"
                    />
                  </Field>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button variant="danger" onClick={() => void review("confirm")} disabled={busy}>Confirm</Button>
                    <Button onClick={() => void review("dismiss")} disabled={busy}>Dismiss</Button>
                    <Button variant="secondary" onClick={() => void review("waive")} disabled={busy}>Waive</Button>
                    <Button variant="secondary" onClick={() => void review("request_information")} disabled={busy}>
                      Request info
                    </Button>
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
