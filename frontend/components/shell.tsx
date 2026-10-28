"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useAuth, type Me } from "@/lib/auth";
import { Spinner } from "@/components/ui";

export function AppShell({ children }: { children: ReactNode }) {
  const { me, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Restoring session…" />
      </div>
    );
  }
  if (!me) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="w-60 shrink-0 border-r border-slate-200 bg-slate-900 text-slate-200">
        <div className="px-5 py-5">
          <div className="text-base font-bold tracking-tight text-white">Resilient AI</div>
          <div className="text-[11px] text-slate-400">AICTE application validation</div>
        </div>
        <nav className="flex flex-col px-3 pb-6 text-sm">
          {navFor(me).map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded px-3 py-2 ${
                  active ? "bg-indigo-600 text-white" : "hover:bg-slate-800"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="absolute bottom-4 w-60 px-5 text-[11px] text-slate-500">
          Demo environment · simulated checks
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <div className="text-sm text-slate-500">
            {pathname === "/" ? "Dashboard" : pathname}
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <div className="text-sm font-medium text-slate-900">{me.full_name}</div>
              <div className="text-xs text-slate-500">
                {me.role} · {me.email}
              </div>
            </div>
            <button
              onClick={async () => {
                await logout();
                router.replace("/login");
              }}
              className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
            >
              Sign out
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

type NavItem = { href: string; label: string };

function navFor(me: Me): NavItem[] {
  const items: NavItem[] = [{ href: "/dashboard", label: "Dashboard" }];
  if (me.role === "institution") {
    items.push({ href: "/applications", label: "My applications" });
    items.push({ href: "/reports", label: "Scrutiny reports" });
    items.push({ href: "/audit", label: "Activity log" });
    return items;
  }
  items.push({ href: "/applications", label: "Applications" });
  items.push({ href: "/findings", label: "Findings queue" });
  items.push({ href: "/verification", label: "Verification" });
  items.push({ href: "/reports", label: "Reports" });
  items.push({ href: "/rules", label: "Compliance rules" });
  items.push({ href: "/audit", label: "Audit trail" });
  if (me.role === "admin") {
    items.push({ href: "/users", label: "Users" });
  }
  return items;
}
