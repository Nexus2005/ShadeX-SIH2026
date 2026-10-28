"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { ApplicationListItem, Institution } from "@/lib/types";
import { Badge, Button, Empty, ErrorBox, Field, Spinner, inputClass } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

export default function ApplicationsPage() {
  const { token } = useAuth();
  const [apps, setApps] = useState<ApplicationListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    const q = statusFilter ? `?status=${statusFilter}` : "";
    apiGet<{ applications: ApplicationListItem[] }>(token, `/applications${q}`)
      .then((d) => setApps(d.applications))
      .catch((e) => setError(String(e)));
  }, [token, statusFilter]);

  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Applications</h1>
          <p className="text-sm text-slate-500">EoA renewals and related filings.</p>
        </div>
        <Button onClick={() => setCreating(true)}>+ New application</Button>
      </div>
      <ErrorBox message={error} />
      <div className="flex gap-2">
        <select
          className={`${inputClass} max-w-xs`}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All statuses</option>
          {["draft", "submitted", "under_review", "additional_info_required", "forwarded_to_authority", "approved", "rejected", "withdrawn"].map((s) => (
            <option key={s} value={s}>{titleCase(s)}</option>
          ))}
        </select>
      </div>
      {!apps ? (
        <Spinner />
      ) : apps.length === 0 ? (
        <Empty>No applications yet{statusFilter ? " for this status" : ""}.</Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2">Reference</th>
                <th className="px-4 py-2">Title</th>
                <th className="px-4 py-2">Institution</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Priority</th>
                <th className="px-4 py-2">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {apps.map((a) => (
                <tr key={a.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-medium">
                    <Link href={`/applications/${a.id}`} className="text-indigo-600 hover:underline">
                      {a.reference_no}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-900">{a.title}</td>
                  <td className="px-4 py-2 text-slate-600">{a.institution_name}</td>
                  <td className="px-4 py-2"><Badge>{a.status}</Badge></td>
                  <td className="px-4 py-2"><Badge kind="severity">{a.priority}</Badge></td>
                  <td className="px-4 py-2 text-slate-600">{fmtDate(a.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <NewApplicationDialog
          token={token}
          onClose={() => setCreating(false)}
          onCreated={load}
        />
      )}
    </div>
  );
}

function NewApplicationDialog({
  token,
  onClose,
  onCreated,
}: {
  token: string | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [institutions, setInstitutions] = useState<Institution[] | null>(null);
  const [form, setForm] = useState({
    institution_id: "",
    category_code: "EOA",
    academic_year: "2026-27",
    title: "",
    description: "",
    status: "draft",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiGet<{ institutions: Institution[] }>(token, "/institutions")
      .then((d) => setInstitutions(d.institutions))
      .catch((e) => setError(String(e)));
  }, [token]);

  useEffect(() => {
    if (institutions?.length && !form.institution_id) {
      setForm((f) => ({ ...f, institution_id: institutions[0].id }));
    }
  }, [institutions, form.institution_id]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await apiSend(token ?? "", "/applications", "POST", {
        ...form,
        title: form.title || "Untitled application",
      });
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">New application</h2>
        <ErrorBox message={error} />
        <div className="space-y-3">
          <Field label="Institution">
            <select
              className={inputClass}
              value={form.institution_id}
              onChange={(e) => setForm({ ...form, institution_id: e.target.value })}
            >
              <option value="">— none —</option>
              {(institutions ?? []).map((i) => (
                <option key={i.id} value={i.id}>{i.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Title">
            <input
              className={inputClass}
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Category">
              <select
                className={inputClass}
                value={form.category_code}
                onChange={(e) => setForm({ ...form, category_code: e.target.value })}
              >
                <option value="EOA">Extension of Approval</option>
              </select>
            </Field>
            <Field label="Academic year">
              <input
                className={inputClass}
                value={form.academic_year}
                onChange={(e) => setForm({ ...form, academic_year: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Description">
            <textarea
              className={inputClass}
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  );
}
