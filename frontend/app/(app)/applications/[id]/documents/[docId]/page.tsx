"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { Application, DocumentRecord, ExtractedField } from "@/lib/types";
import { Badge, Card, Empty, ErrorBox, Spinner, inputClass } from "@/components/ui";

type DetailPayload = Application & {
  documents: DocumentRecord[];
};

export default function DocumentDetailPage() {
  const params = useParams<{ id: string; docId: string }>();
  const { id: appId, docId } = params;
  const { token } = useAuth();
  const [app, setApp] = useState<DetailPayload | null>(null);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [fields, setFields] = useState<ExtractedField[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const d = await apiGet<DetailPayload>(token, `/applications/${appId}`);
      setApp(d);
      const mine = d.documents.find((x) => x.id === docId) ?? null;
      setDoc(mine);
      const ex = await apiGet<{ extractions: ExtractedField[] }>(
        token,
        `/documents/${docId}/extractions`,
      );
      setFields(ex.extractions);
    } catch (e) {
      setError(String(e));
    }
  }, [appId, docId, token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveField(field: ExtractedField) {
    const value = editValue[field.id];
    if (!value) return;
    setSaving(field.id);
    setError(null);
    try {
      await apiSend(token ?? "", `/fields/${field.id}`, "PATCH", { corrected_value: value });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  }

  if (!fields) {
    return (
      <div className="space-y-4">
        <ErrorBox message={error} />
        <Spinner label="Loading extraction…" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500">
        <Link href="/applications" className="hover:underline">Applications</Link> /{" "}
        <Link href={`/applications/${appId}`} className="hover:underline">
          {app?.reference_no}
        </Link>{" "}
        / document details
      </div>
      <ErrorBox message={error} />
      {!doc ? (
        <Empty>Document not found on this application.</Empty>
      ) : (
        <>
          <Card title={doc.title}>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge>{doc.status}</Badge>
              <span className="text-slate-600">
                declared {doc.declared_category_code ?? "—"} → detected {doc.detected_category_code ?? "—"}
              </span>
              {doc.classification_method && (
                <span className="text-xs text-slate-400">via {doc.classification_method}</span>
              )}
            </div>
          </Card>
          <Card title={`Extracted fields (${fields.length})`}>
            {fields.length === 0 ? (
              <Empty>No fields extracted for this document.</Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">Field</th>
                      <th className="py-2 pr-3">Raw value</th>
                      <th className="py-2 pr-3">Normalized</th>
                      <th className="py-2 pr-3">Method</th>
                      <th className="py-2 pr-3">Status</th>
                      <th className="py-2 pr-3">Correction</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {fields.map((f) => (
                      <tr key={f.id}>
                        <td className="py-2 pr-3">
                          <span className="font-medium text-slate-900">{f.label}</span>
                          <span className="block font-mono text-[11px] text-slate-400">{f.field_name}</span>
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs">{f.raw_value ?? "—"}</td>
                        <td className="py-2 pr-3 font-mono text-xs">
                          {f.normalized_value != null ? String(f.normalized_value) : "—"}
                        </td>
                        <td className="py-2 pr-3 text-slate-600">{f.extraction_method ?? "—"}</td>
                        <td className="py-2 pr-3">
                          <Badge>{f.status}</Badge>
                          {f.corrected_value && (
                            <span className="ml-1 text-xs text-emerald-700">→ {f.corrected_value}</span>
                          )}
                        </td>
                        <td className="py-2 pr-3">
                          <div className="flex items-center gap-1">
                            <input
                              className={`${inputClass} w-40`}
                              placeholder="corrected value"
                              value={editValue[f.id] ?? ""}
                              onChange={(e) => setEditValue({ ...editValue, [f.id]: e.target.value })}
                            />
                            <button
                              className="text-xs text-indigo-600 hover:underline disabled:text-slate-400"
                              onClick={() => void saveField(f)}
                              disabled={saving === f.id || !editValue[f.id]}
                            >
                              {saving === f.id ? "Saving…" : "Save"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
