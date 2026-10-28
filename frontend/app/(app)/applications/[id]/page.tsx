"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { API_BASE, apiGet, apiSend, useAuth } from "@/lib/auth";
import type {
  Application,
  DocumentRecord,
  Finding,
  HistoryEntry,
  ReportRecord,
  VerificationAttempt,
} from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBox,
  Field,
  Spinner,
  Tabs,
  inputClass,
} from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

type DetailPayload = Application & {
  documents: DocumentRecord[];
  latest_validation_run: { id: string; status: string; summary: Record<string, unknown> | null; started_at: string } | null;
  open_findings_count: number;
};

const STATUS_TARGETS = [
  "submitted",
  "under_review",
  "additional_info_required",
  "forwarded_to_authority",
  "approved",
  "rejected",
  "withdrawn",
];

export default function ApplicationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { token, me } = useAuth();
  const [tab, setTab] = useState("overview");
  const [app, setApp] = useState<DetailPayload | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [verify, setVerify] = useState<VerificationAttempt[] | null>(null);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [reports, setReports] = useState<ReportRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const isStaff = me?.role === "admin" || me?.role === "reviewer" || me?.role === "auditor";
  const canReview = me?.role === "admin" || me?.role === "reviewer";

  const loadApp = useCallback(async () => {
    if (!token) return;
    try {
      const d = await apiGet<DetailPayload>(token, `/applications/${id}`);
      setApp(d);
    } catch (e) {
      setError(String(e));
    }
  }, [id, token]);

  const loadSide = useCallback(async () => {
    if (!token) return;
    apiGet<{ findings: Finding[] }>(token, `/findings?application_id=${id}`)
      .then((d) => setFindings(d.findings))
      .catch(() => setFindings([]));
    if (isStaff) {
      apiGet<{ attempts: VerificationAttempt[] }>(token, `/verification?application_id=${id}`)
        .then((d) => setVerify(d.attempts))
        .catch(() => setVerify([]));
    }
    apiGet<{ history: HistoryEntry[] }>(token, `/applications/${id}/history`)
      .then((d) => setHistory(d.history))
      .catch(() => setHistory([]));
    apiGet<{ reports: ReportRecord[] }>(token, `/reports?application_id=${id}`)
      .then((d) => setReports(d.reports))
      .catch(() => setReports([]));
  }, [id, token, isStaff]);

  useEffect(() => {
    void loadApp();
    void loadSide();
  }, [loadApp, loadSide]);

  async function runAction(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await loadApp();
      await loadSide();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const findingsTabs = [
    { key: "overview", label: "Overview" },
    { key: "documents", label: "Documents", badge: app?.documents.length },
    { key: "findings", label: "Findings", badge: findings?.length },
    { key: "verification", label: "Verification", badge: verify?.length },
    { key: "history", label: "History" },
    { key: "reports", label: "Reports", badge: reports?.length },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-slate-500">
            <Link href="/applications" className="hover:underline">Applications</Link> / {app?.reference_no}
          </div>
          <h1 className="text-xl font-semibold text-slate-900">
            {app ? <Badge>{app.status}</Badge> : null} {app?.title ?? "Application"}
          </h1>
          {app && (
            <p className="mt-1 text-sm text-slate-500">
              {app.institution?.name} · {app.category_code} · {app.academic_year} ·
              submitted {fmtDate(app.submitted_at)}
            </p>
          )}
        </div>
        {canReview && app && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              disabled={busy != null}
              onClick={() =>
                runAction("validate", async () => {
                  await apiSend(token ?? "", `/applications/${id}/validate`, "POST");
                })
              }
            >
              {busy === "validate" ? "Validating…" : "Run validation"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy != null}
              onClick={() =>
                runAction("verify", async () => {
                  await apiSend(token ?? "", `/applications/${id}/verify`, "POST");
                })
              }
            >
              {busy === "verify" ? "Verifying…" : "Re-run verification"}
            </Button>
            <Button
              disabled={busy != null}
              onClick={() =>
                runAction("report", async () => {
                  await apiSend(token ?? "", `/applications/${id}/reports`, "POST");
                })
              }
            >
              {busy === "report" ? "Generating…" : "Generate scrutiny report"}
            </Button>
          </div>
        )}
      </div>
      <ErrorBox message={error} />
      {!app ? (
        <Spinner label="Loading application…" />
      ) : (
        <Tabs tabs={findingsTabs} active={tab} onChange={setTab} />
      )}
      {app && tab === "overview" && (
        <OverviewTab app={app} token={token} canReview={canReview} busy={busy} runAction={runAction} reload={loadApp} />
      )}
      {app && tab === "documents" && <DocumentsTab app={app} token={token} reload={loadApp} canWrite={canReview || me?.role === "institution"} />}
      {app && tab === "findings" && <Findings findings={findings} canReview={canReview} token={token} />}
      {app && tab === "verification" && <VerificationTab attempts={verify} />}
      {app && tab === "history" && <HistoryTab history={history} />}
      {app && tab === "reports" && <ReportsTab reports={reports} token={token} onGenerate={canReview ? () => runAction("report", async () => { await apiSend(token ?? "", `/applications/${id}/reports`, "POST"); }) : null} busy={busy} />}
    </div>
  );
}

/* ------------------------------------------------------------------ overview */

function OverviewTab({
  app,
  token,
  canReview,
  busy,
  runAction,
  reload,
}: {
  app: DetailPayload;
  token: string | null;
  canReview: boolean;
  busy: string | null;
  runAction: (key: string, fn: () => Promise<void>) => Promise<void>;
  reload: () => Promise<void>;
}) {
  const [target, setTarget] = useState(STATUS_TARGETS[0]);
  const [reason, setReason] = useState("");
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Application" className="lg:col-span-2">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Row label="Reference" value={app.reference_no} />
          <Row label="Status" value={<Badge>{app.status}</Badge>} />
          <Row label="Priority" value={<Badge kind="severity">{app.priority}</Badge>} />
          <Row label="Category" value={app.category_code} />
          <Row label="Academic year" value={app.academic_year} />
          <Row label="Open findings" value={String(app.open_findings_count)} />
          <Row label="Last validated" value={fmtDate(app.last_validated_at)} />
          <Row label="Institution" value={app.institution?.name} />
        </dl>
        {app.latest_validation_run && (
          <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-3 text-sm">
            <div className="font-medium text-slate-800">
              Latest validation run — {fmtDate(app.latest_validation_run.started_at)}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-600">
              {JSON.stringify(app.latest_validation_run.summary, null, 1)}
            </div>
          </div>
        )}
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Declared data</h3>
          <div className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {Object.entries(app.declared_data).length === 0 ? (
              <span className="text-slate-400">None</span>
            ) : (
              Object.entries(app.declared_data).map(([k, v]) => (
                <Row key={k} label={titleCase(k)} value={String(v)} />
              ))
            )}
          </div>
        </div>
      </Card>
      {canReview && (
        <Card title="Workflow actions">
          <div className="space-y-3 text-sm">
            <Field label="Change status">
              <select className={inputClass} value={target} onChange={(e) => setTarget(e.target.value)}>
                {STATUS_TARGETS.map((s) => (
                  <option key={s} value={s}>{titleCase(s)}</option>
                ))}
              </select>
            </Field>
            <Field label="Reason">
              <input className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional note" />
            </Field>
            <Button
              variant="secondary"
              disabled={busy != null}
              onClick={() =>
                runAction("status", async () => {
                  await apiSend(token ?? "", `/applications/${app.id}/status`, "POST", {
                    to_status: target,
                    reason: reason || undefined,
                  });
                  await reload();
                })
              }
            >
              {busy === "status" ? "Applying…" : "Apply status change"}
            </Button>
            <p className="text-xs text-slate-500">
              Validation and verification run on the whole application; findings update and status finalizes by rule.
            </p>
          </div>
        </Card>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-slate-900">{value ?? "—"}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------ documents */

function DocumentsTab({
  app,
  token,
  reload,
  canWrite,
}: {
  app: DetailPayload;
  token: string | null;
  reload: () => Promise<void>;
  canWrite: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busyDoc, setBusyDoc] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadCat, setUploadCat] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", file);
        if (uploadCat) fd.append("declared_category_code", uploadCat);
        const res = await fetch(`${API_BASE}/applications/${app.id}/documents`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: fd,
        });
        if (!res.ok) {
          const j = await res.json().catch(() => null);
          throw new Error(j?.detail ?? `Upload failed (${res.status})`);
        }
      }
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function process(docId: string) {
    setBusyDoc(docId);
    setError(null);
    try {
      await apiSend(token ?? "", `/documents/${docId}/process`, "POST");
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyDoc(null);
    }
  }

  async function stream(docId: string, filename: string) {
    const res = await fetch(`${API_BASE}/documents/${docId}/stream`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      setError(`Preview failed (${res.status})`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    void filename;
  }

  return (
    <div className="space-y-4">
      <ErrorBox message={error} />
      {canWrite && (
        <Card title="Upload documents">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Declared category (optional)">
              <select
                className={`${inputClass} w-72`}
                value={uploadCat}
                onChange={(e) => setUploadCat(e.target.value)}
              >
                <option value="">Auto-detect</option>
                {["fire_noc", "aicte_letter", "land_record", "building_certificate", "lab_photo", "faculty_list"].map(
                  (c) => (
                    <option key={c} value={c}>{titleCase(c)}</option>
                  ),
                )}
              </select>
            </Field>
            <div>
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".pdf,.png,.jpg,.jpeg"
                className="text-sm"
                onChange={(e) => void upload(e.target.files)}
                disabled={uploading}
              />
              <p className="mt-1 text-xs text-slate-500">PDF / PNG / JPEG. Processing starts after upload.</p>
            </div>
            {uploading && <Spinner label="Uploading…" />}
          </div>
        </Card>
      )}
      <Card title={`Documents (${app.documents.length})`}>
        {app.documents.length === 0 ? (
          <Empty>No documents uploaded yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Document</th>
                  <th className="py-2 pr-3">Declared → detected</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Version</th>
                  <th className="py-2 pr-3">Updated</th>
                  <th className="py-2 pr-3">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {app.documents.map((d) => (
                  <tr key={d.id}>
                    <td className="py-2 pr-3 font-medium text-slate-900">{d.title}</td>
                    <td className="py-2 pr-3 text-slate-600">
                      {d.declared_category_code ?? "—"} → {d.detected_category_code ?? "—"}
                      {d.classification_method && (
                        <span className="ml-1 text-xs text-slate-400">({d.classification_method})</span>
                      )}
                    </td>
                    <td className="py-2 pr-3"><Badge>{d.status}</Badge></td>
                    <td className="py-2 pr-3">v{d.version_no}</td>
                    <td className="py-2 pr-3 text-slate-600">{fmtDate(d.updated_at)}</td>
                    <td className="py-2 pr-3">
                      <div className="flex gap-2">
                        <button
                          className="text-xs text-indigo-600 hover:underline"
                          onClick={() => void stream(d.id, d.title)}
                        >
                          Preview
                        </button>
                        <Link href={`/applications/${app.id}/documents/${d.id}`} className="text-xs text-indigo-600 hover:underline">
                          Details
                        </Link>
                        {canWrite && (
                          <button
                            className="text-xs text-indigo-600 hover:underline disabled:text-slate-400"
                            onClick={() => void process(d.id)}
                            disabled={busyDoc === d.id}
                          >
                            {busyDoc === d.id ? "Processing…" : "Reprocess"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ findings */

function Findings({
  findings,
  canReview,
  token,
}: {
  findings: Finding[] | null;
  canReview: boolean;
  token: string | null;
}) {
  const [selected, setSelected] = useState<Finding | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [severityFilter, setSeverityFilter] = useState("");
  const [detail, setDetail] = useState<Finding | null>(null);

  const loadDetail = useCallback(
    async (f: Finding) => {
      setSelected(f);
      setDetail(f);
      try {
        const d = await apiGet<Finding>(token ?? "", `/findings/${f.id}`);
        setDetail(d);
      } catch {
        /* keep cached */
      }
    },
    [token],
  );

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
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!findings) return <Spinner />;
  const visible = severityFilter
    ? findings.filter((f) => f.severity === severityFilter)
    : findings;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-3">
        <ErrorBox message={error} />
        <div className="flex items-center gap-2">
          <select
            className={`${inputClass} max-w-48`}
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
          >
            <option value="">All severities</option>
            {["critical", "high", "medium", "low", "info"].map((s) => (
              <option key={s} value={s}>{titleCase(s)}</option>
            ))}
          </select>
          <span className="text-sm text-slate-500">{visible.length} findings</span>
        </div>
        {visible.length === 0 ? (
          <Empty>No findings recorded for this application.</Empty>
        ) : (
          <ul className="space-y-2">
            {visible.map((f) => (
              <li key={f.id}>
                <button
                  onClick={() => void loadDetail(f)}
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
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <Badge>{f.status}</Badge>
                    <span>{f.category}</span>
                    <span>{f.finding_code}</span>
                    {f.is_simulated && <span className="text-amber-600">simulated</span>}
                    {f.is_advisory && <span className="text-indigo-500">advisory</span>}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        {!detail ? (
          <Empty>Select a finding to see details and evidence.</Empty>
        ) : (
          <Card title={detail.title}>
            <dl className="grid grid-cols-1 gap-x-6 text-sm sm:grid-cols-2">
              <Row label="Code" value={detail.finding_code} />
              <Row label="Status" value={<Badge>{detail.status}</Badge>} />
              <Row label="Severity" value={<Badge kind="severity">{detail.severity}</Badge>} />
              <Row label="Confidence" value={detail.confidence != null ? `${(detail.confidence * 100).toFixed(0)}%` : "—"} />
              <Row label="Expected" value={detail.expected_value} />
              <Row label="Observed" value={detail.observed_value} />
              <Row label="Detected" value={fmtDate(detail.last_detected_at)} />
              <Row label="Method" value={detail.detection_method} />
            </dl>
            <p className="mt-3 text-sm text-slate-700">{detail.description}</p>
            {detail.recommended_action && (
              <p className="mt-2 text-sm text-slate-600">
                <span className="font-medium">Recommended:</span> {detail.recommended_action}
              </p>
            )}
            <div className="mt-4">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
              {!detail.evidence?.length ? (
                <Empty>No attached evidence.</Empty>
              ) : (
                <ul className="space-y-3">
                  {detail.evidence.map((ev) => (
                    <li key={ev.id} className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
                      <div className="mb-1 flex justify-between text-xs text-slate-500">
                        <span>{ev.role}</span>
                        <span>{ev.page_no != null ? `page ${ev.page_no}` : ""}</span>
                      </div>
                      {ev.excerpt && (
                        <blockquote className="border-l-2 border-indigo-300 pl-2 font-mono text-xs text-slate-700">
                          {ev.excerpt}
                        </blockquote>
                      )}
                      {ev.document_id && (
                        <div className="mt-1 text-xs text-indigo-600">
                          {ev.document_id}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
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
                  <Button variant="danger" onClick={() => void review("confirm")} disabled={busy}>
                    Confirm
                  </Button>
                  <Button onClick={() => void review("dismiss")} disabled={busy} variant="primary">
                    Dismiss
                  </Button>
                  <Button variant="secondary" onClick={() => void review("waive")} disabled={busy}>
                    Waive
                  </Button>
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
  );
}

/* ------------------------------------------------------------------ verification */

function VerificationTab({ attempts }: { attempts: VerificationAttempt[] | null }) {
  if (!attempts) return <Spinner />;
  return (
    <Card title="Simulated verification attempts">
      {attempts.length === 0 ? (
        <Empty>No verification attempts recorded (staff-only view).</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-3">Provider</th>
                <th className="py-2 pr-3">Reference</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Compared fields</th>
                <th className="py-2 pr-3">Requested</th>
                <th className="py-2 pr-3">Response</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {attempts.map((a) => (
                <tr key={a.id}>
                  <td className="py-2 pr-3 font-medium">{a.provider_code}</td>
                  <td className="py-2 pr-3 text-slate-600">{a.reference_no ?? "—"}</td>
                  <td className="py-2 pr-3"><Badge kind="verification">{a.status}</Badge></td>
                  <td className="py-2 pr-3 font-mono text-xs text-slate-600">
                    {Object.entries(a.compared_fields ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"}
                  </td>
                  <td className="py-2 pr-3 text-slate-600">{fmtDate(a.requested_at)}</td>
                  <td className="py-2 pr-3">
                    <details>
                      <summary className="cursor-pointer text-xs text-indigo-600">payload</summary>
                      <pre className="mt-1 max-w-md overflow-auto rounded bg-slate-50 p-2 font-mono text-[11px]">
                        {JSON.stringify(a.response, null, 1)}
                      </pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ history */

function HistoryTab({ history }: { history: HistoryEntry[] | null }) {
  if (!history) return <Spinner />;
  return (
    <Card title="Status history">
      {history.length === 0 ? (
        <Empty>No transitions recorded.</Empty>
      ) : (
        <ol className="space-y-3">
          {history.map((h, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />
              <div>
                <div className="font-medium text-slate-900">
                  {titleCase(h.from ?? "—")} <span className="text-slate-400">→</span> {titleCase(h.to)}
                </div>
                <div className="text-xs text-slate-500">
                  {fmtDate(h.at)} {h.actor ? `· ${h.actor}` : ""} {h.reason ? `· ${h.reason}` : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ reports */

function ReportsTab({
  reports,
  token,
  onGenerate,
  busy,
}: {
  reports: ReportRecord[] | null;
  token: string | null;
  onGenerate: (() => Promise<void>) | null;
  busy: string | null;
}) {
  const [error, setError] = useState<string | null>(null);
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
      a.download = `scrutiny-report-${id}.pdf`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <Card
      title="Scrutiny reports"
      actions={
        onGenerate && (
          <Button onClick={onGenerate} disabled={busy != null}>
            {busy === "report" ? "Generating…" : "Generate report"}
          </Button>
        )
      }
    >
      <ErrorBox message={error} />
      {!reports ? (
        <Spinner />
      ) : reports.length === 0 ? (
        <Empty>No reports generated yet.</Empty>
      ) : (
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-2 pr-3">Report no</th>
              <th className="py-2 pr-3">Type</th>
              <th className="py-2 pr-3">Generated</th>
              <th className="py-2 pr-3">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {reports.map((r) => (
              <tr key={r.id}>
                <td className="py-2 pr-3 font-mono text-xs">{r.reference_code}</td>
                <td className="py-2 pr-3">{titleCase(r.report_type)}</td>
                <td className="py-2 pr-3 text-slate-600">{fmtDate(r.generated_at)}</td>
                <td className="py-2 pr-3">
                  <button className="text-xs text-indigo-600 hover:underline" onClick={() => void download(r.id)}>
                    Download PDF
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
