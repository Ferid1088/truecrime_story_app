"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";
import {
  Compass,
  Database,
  FlaskConical,
  FolderOpen,
  LayoutDashboard,
  Menu,
  Moon,
  PenLine,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/config";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/discover", label: "Discover", icon: Compass },
  { href: "/cases", label: "Cases", icon: FolderOpen },
  { href: "/research", label: "Research", icon: FlaskConical },
  { href: "/studio", label: "Story Studio", icon: PenLine },
  { href: "/database", label: "Database", icon: Database },
  { href: "/settings", label: "Settings", icon: Settings },
];

function ApiStatus() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    const check = () =>
      fetch(`${API_BASE}/health`)
        .then((r) => !cancelled && setOk(r.ok))
        .catch(() => !cancelled && setOk(false));
    check();
    const t = setInterval(check, 15000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  return (
    <div className="flex items-center gap-2 px-2 text-xs text-muted-foreground">
      <span
        className={cn(
          "size-1.5 rounded-full",
          ok === null ? "bg-muted-foreground" : ok ? "bg-emerald-500" : "bg-rose-500",
        )}
      />
      {ok === null ? "Checking API…" : ok ? "API connected" : "API offline"}
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex size-6 items-center justify-center rounded bg-primary text-[11px] font-bold text-primary-foreground">
        TC
      </div>
      <div>
        <p className="text-sm font-semibold leading-none">TrueCrime Studio</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">Research &amp; Writing</p>
      </div>
    </div>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
              active
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <button
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      className="flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
    >
      <Sun className="size-4 dark:hidden" />
      <Moon className="hidden size-4 dark:block" />
      Toggle theme
    </button>
  );
}

export function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    drawerRef.current?.querySelector<HTMLElement>("a, button")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-56 flex-col border-r border-border bg-subtle md:flex">
        <div className="flex h-14 items-center border-b border-border px-4">
          <Brand />
        </div>
        <NavLinks />
        <div className="space-y-2 border-t border-border p-3">
          <ApiStatus />
          <ThemeToggle />
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-40 flex h-12 items-center justify-between border-b border-border bg-subtle px-3 md:hidden">
        <Brand />
        <button
          onClick={() => setMobileOpen(true)}
          aria-label="Open navigation menu"
          className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Menu className="size-5" />
        </button>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <div
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-border bg-subtle"
          >
            <div className="flex h-12 items-center justify-between border-b border-border px-3">
              <Brand />
              <button
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation menu"
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-5" />
              </button>
            </div>
            <NavLinks onNavigate={() => setMobileOpen(false)} />
            <div className="space-y-2 border-t border-border p-3">
              <ApiStatus />
              <ThemeToggle />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
