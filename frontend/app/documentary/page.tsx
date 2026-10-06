"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, langLabel } from "@/lib/format";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { CaseStatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";

export default function DocumentaryPage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const { data, error, loading, refetch } = useApi(
    () => api.listCases({ q: debouncedQ || undefined }),
    [debouncedQ],
  );

  return (
    <div>
      <PageHeader
        title="Documentary"
        description="Turn a finished story into a narrated film in every language. Every documentary runs 45–120 minutes; pilots render just the opening."
      />

      <div className="relative mb-4 w-full sm:w-64">
        <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search cases…"
          aria-label="Search cases"
          className="pl-8"
        />
      </div>

      {loading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}
      {error && <ErrorState message={error} onRetry={refetch} />}
      {data && data.length === 0 && (
        <EmptyState
          title={debouncedQ ? "No cases match" : "No cases yet"}
          description={
            debouncedQ
              ? "Try a different search."
              : "Discover or create a case, research it and write a master story first."
          }
        />
      )}
      {data && data.length > 0 && (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((c) => (
            <li key={c.id}>
              <Link
                href={`/documentary/${c.id}`}
                className="group flex h-full items-start justify-between gap-3 rounded-lg border border-border bg-card p-4 outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium" title={c.title}>
                    {c.title}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <CaseStatusBadge status={c.status} />
                    <span className="text-xs text-muted-foreground">{langLabel(c.language)}</span>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {c.story_versions > 0
                      ? `${c.story_versions} story version${c.story_versions === 1 ? "" : "s"}`
                      : "No story yet"}{" "}
                    · {formatDateTime(c.last_activity)}
                  </p>
                </div>
                <ChevronRight
                  className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                  aria-hidden
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
