export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-200 text-slate-700",
  submitted: "bg-blue-100 text-blue-700",
  under_review: "bg-indigo-100 text-indigo-700",
  additional_info_required: "bg-amber-100 text-amber-800",
  forwarded_to_authority: "bg-cyan-100 text-cyan-700",
  approved: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
  withdrawn: "bg-slate-200 text-slate-600",
  uploaded: "bg-blue-100 text-blue-700",
  processing: "bg-amber-100 text-amber-800",
  processed: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
  unreadable: "bg-rose-100 text-rose-700",
  open: "bg-rose-100 text-rose-700",
  confirmed: "bg-rose-200 text-rose-900",
  dismissed: "bg-emerald-100 text-emerald-700",
  waived: "bg-slate-200 text-slate-600",
  resolved: "bg-emerald-100 text-emerald-700",
};

export const SEVERITY_COLORS: Record<string, string> = {
  info: "bg-slate-200 text-slate-700",
  low: "bg-sky-100 text-sky-700",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-orange-200 text-orange-900",
  critical: "bg-rose-200 text-rose-900",
};

export const VERIFICATION_COLORS: Record<string, string> = {
  verified: "bg-emerald-100 text-emerald-700",
  mismatch: "bg-rose-100 text-rose-700",
  expired: "bg-amber-100 text-amber-800",
  unavailable: "bg-orange-100 text-orange-800",
  not_supported: "bg-slate-200 text-slate-600",
  manual_review_required: "bg-indigo-100 text-indigo-700",
  pending: "bg-slate-200 text-slate-700",
};
