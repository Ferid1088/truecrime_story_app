"use client";

import { useCallback, useMemo, useState } from "react";
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
import { BatchPanel } from "@/components/documentary/batch";
import { usePolling } from "@/components/documentary/hooks";
import { RecentJobs, SchedulerCard } from "@/components/documentary/scheduler";

const SCHEDULER_POLL_MS = 5000;

export default function DocumentaryPage() {
  const [q, setQ] = useState("");
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const cases = useApi(() => api.listCases(), []);
  const settings = useApi(() => api.documentarySettings(), []);
  const scheduler = useApi(() => api.documentaryScheduler(), [tick], { keepPrevious: true });

  const active = scheduler.data ? [...scheduler.data.running, ...scheduler.data.queued] : [];
  usePolling(scheduler.refetch, active.length > 0, SCHEDULER_POLL_MS);
  // Recent runs refresh when a job starts, finishes or changes state.
  const activeSignature = active.map((j) => `${j.id}:${j.status}`).join(",");

  const titles = useMemo(() => new Map((cases.data ?? []).map((c) => [c.id, c.title])), [cases.data]);
  const needle = q.trim().toLowerCase();
  const shown = (cases.data ?? []).filter((c) => !needle || c.title.toLowerCase().includes(needle));
  const fatal = cases.error ?? settings.error;

  return (
    <div>
      <PageHeader
        title="Documentary"
        description="Turn a finished story into a narrated film in every language. Every documentary runs 45–120 minutes; pilots render just the opening."
      />

      {fatal ? (
        <ErrorState
          message={fatal}
          onRetry={() => {
            cases.refetch();
            settings.refetch();
          }}
        />
      ) : !cases.data || !settings.data ? (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_360px]">
          <Skeleton className="h-96" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <>
          <div className="mb-8 grid grid-cols-1 items-start gap-4 xl:grid-cols-[1fr_360px]">
            <BatchPanel cases={cases.data} settings={settings.data} titles={titles} onStarted={refresh} />
            <div className="space-y-4">
              <SchedulerCard
                data={scheduler.data}
                error={scheduler.error}
                onRetry={scheduler.refetch}
                titles={titles}
                configured={settings.data.concurrency}
              />
              <RecentJobs refreshKey={`${tick}|${activeSignature}`} />
            </div>
          </div>

          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-medium">Open one documentary</h2>
            <div className="relative w-full sm:w-64">
              <Search
                className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search cases…"
                aria-label="Search cases"
                className="pl-8"
              />
            </div>
          </div>

          {shown.length === 0 ? (
            <EmptyState
              title={needle ? "No cases match" : "No cases yet"}
              description={
                needle
                  ? "Try a different search."
                  : "Discover or create a case — a documentary can start from zero with research and the master story."
              }
            />
          ) : (
            <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {shown.map((c) => (
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
        </>
      )}
    </div>
  );
}
