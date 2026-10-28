"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { RuleRecord } from "@/lib/types";
import { Badge, Button, Card, Empty, ErrorBox, Field, Spinner, inputClass } from "@/components/ui";
import { titleCase } from "@/lib/format";

export default function RulesPage() {
  const { token, me } = useAuth();
  const [rules, setRules] = useState<RuleRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const isAdmin = me?.role === "admin";

  const load = useCallback(() => {
    apiGet<{ rules: RuleRecord[] }>(token, "/rules")
      .then((d) => setRules(d.rules))
      .catch((e) => setError(String(e)));
  }, [token]);

  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Compliance rules</h1>
          <p className="text-sm text-slate-500">
            Versioned deterministic rules. Seeded demo rules are marked and outcomes never use an LLM.
          </p>
        </div>
        {isAdmin && <Button onClick={() => setCreating(true)}>+ New version</Button>}
      </div>
      <ErrorBox message={error} />
      {!rules ? (
        <Spinner />
      ) : rules.length === 0 ? (
        <Empty>No rules configured.</Empty>
      ) : (
        <div className="grid gap-3">
          {rules.map((r) => (
            <Card key={r.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-slate-500">{r.rule_code}</span>
                    <Badge kind="severity">{r.current_version?.severity ?? "medium"}</Badge>
                    {r.current_version?.is_demo_rule && <Badge>demo rule</Badge>}
                    <Badge>{r.is_active ? "active" : "inactive"}</Badge>
                  </div>
                  <h3 className="mt-1 font-semibold text-slate-900">{r.name}</h3>
                  <p className="mt-0.5 max-w-xl text-sm text-slate-600">{r.current_version?.description}</p>
                  <div className="mt-2 font-mono text-xs text-slate-500">
                    {JSON.stringify(r.current_version?.definition ?? {})}
                  </div>
                  {r.current_version?.source_document && (
                    <div className="mt-1 text-xs text-slate-400">
                      source: {r.current_version.source_document}
                      {r.current_version.source_clause ? ` · ${r.current_version.source_clause}` : ""}
                    </div>
                  )}
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div>
                    v{r.current_version?.version ?? "—"} of {r.version_count}
                  </div>
                  <div className="uppercase">{titleCase(r.category ?? "rule")}</div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
      {creating && (
        <NewVersionDialog token={token} rules={rules ?? []} onClose={() => setCreating(false)} onCreated={load} />
      )}
    </div>
  );
}

function NewVersionDialog({
  token,
  rules,
  onClose,
  onCreated,
}: {
  token: string | null;
  rules: RuleRecord[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [ruleId, setRuleId] = useState(rules[0]?.id ?? "");
  const [severity, setSeverity] = useState("medium");
  const [maxAgeDays, setMaxAgeDays] = useState("365");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selected = rules.find((r) => r.id === ruleId);
  const evaluator = (selected?.current_version?.definition as Record<string, unknown> | undefined)?.evaluator;
  const baseParams = (selected?.current_version?.definition as Record<string, unknown> | undefined) ?? {};
  const [presetParams, setPresetParams] = useState<Record<string, unknown>>(baseParams);

  useEffect(() => {
    const def = (selected?.current_version?.definition as Record<string, unknown> | undefined) ?? {};
    setPresetParams(def);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ruleId]);

  const params: Record<string, unknown> =
    evaluator === "document_expiry" && maxAgeDays
      ? { ...presetParams, max_age_days: Number(maxAgeDays) || 365 }
      : presetParams;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await apiSend(token ?? "", `/rules/${ruleId}/versions`, "POST", { severity, params });
      onCreated();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">New rule version</h2>
        <ErrorBox message={error} />
        <div className="space-y-3">
          <Field label="Rule">
            <select className={inputClass} value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
              {rules.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.rule_code} — {r.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Severity for this version">
            <select className={inputClass} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {["info", "low", "medium", "high", "critical"].map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </select>
          </Field>
          {evaluator === "document_expiry" && (
            <Field label="Max age (days)">
              <input
                className={inputClass}
                type="number"
                value={maxAgeDays}
                onChange={(e) => setMaxAgeDays(e.target.value)}
              />
            </Field>
          )}
          <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs">
            <div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Version params</div>
            <pre className="overflow-auto font-mono text-slate-700">{JSON.stringify(params, null, 1)}</pre>
            <p className="mt-1 text-slate-500">
              New versions are immutable and become effective immediately; the previous version is retained for the audit trail.
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !ruleId}>
            {busy ? "Creating…" : "Create version"}
          </Button>
        </div>
      </div>
    </div>
  );
}
