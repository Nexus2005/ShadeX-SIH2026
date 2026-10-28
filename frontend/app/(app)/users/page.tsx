"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, useAuth } from "@/lib/auth";
import type { UserRecord } from "@/lib/types";
import { Badge, Button, Empty, ErrorBox, Spinner, inputClass } from "@/components/ui";

const ROLES = ["admin", "reviewer", "institution", "auditor"];

export default function UsersPage() {
  const { token } = useAuth();
  const [users, setUsers] = useState<UserRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    apiGet<{ users: UserRecord[] }>(token, "/auth/users")
      .then((d) => setUsers(d.users))
      .catch((e) => setError(String(e)));
  }, [token]);

  useEffect(load, [load]);

  async function changeRole(user: UserRecord, role: string) {
    setBusy(user.id);
    setError(null);
    try {
      await apiSend(token ?? "", `/auth/users/${user.id}`, "PATCH", { role });
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function toggleActive(user: UserRecord) {
    setBusy(user.id);
    setError(null);
    try {
      await apiSend(token ?? "", `/auth/users/${user.id}`, "PATCH", { is_active: !user.is_active });
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
        <h1 className="text-xl font-semibold text-slate-900">Users</h1>
        <p className="text-sm text-slate-500">Role-based access: admin, reviewer, institution, auditor.</p>
      </div>
      <ErrorBox message={error} />
      {!users ? (
        <Spinner />
      ) : users.length === 0 ? (
        <Empty>No users listed.</Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Email</th>
                <th className="px-4 py-2">Role</th>
                <th className="px-4 py-2">Active</th>
                <th className="px-4 py-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {u.full_name}
                    {u.is_demo_account && <span className="ml-2 rounded bg-slate-200 px-1.5 text-xs text-slate-600">demo</span>}
                  </td>
                  <td className="px-4 py-2 text-slate-600">{u.email}</td>
                  <td className="px-4 py-2">
                    <select
                      className={`${inputClass} w-32`}
                      value={u.role}
                      onChange={(e) => void changeRole(u, e.target.value)}
                      disabled={busy === u.id}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-2 text-slate-600">
                    <Badge>{u.is_active ? "active" : "suspended"}</Badge>
                  </td>
                  <td className="px-4 py-2">
                    <Button
                      variant="secondary"
                      disabled={busy === u.id}
                      onClick={() => void toggleActive(u)}
                    >
                      {u.is_active ? "Suspend" : "Activate"}
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
