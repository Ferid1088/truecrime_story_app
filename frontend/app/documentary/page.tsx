"use client";

import { useCallback, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { CaseOverview } from "@/components/case/overview";
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
  const resolutions = useMemo(
    () => new Map((cases.data ?? []).map((c) => [c.id, c.resolution_status])),
    [cases.data],
  );
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
            </div>
          </div>

          <div className="mb-8">
            <RecentJobs refreshKey={`${tick}|${activeSignature}`} resolutions={resolutions} />
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
                  : "Discover or create a case — a documentary can start from zero with research and the language pipeline."
              }
            />
          ) : (
            <CaseOverview
              idPrefix="documentary"
              dateLabel="Created"
              href={(id) => `/documentary/${id}`}
              items={shown.map((c) => ({
                id: c.id,
                title: c.title,
                date: c.created_at,
                archived: c.archived,
                archiveReason: c.archive_reason,
                note: c.story_versions > 0 ? `${c.story_versions} story version${c.story_versions === 1 ? "" : "s"}` : "No story yet",
              }))}
              onArchive={async (id) => {
                await api.archiveCase(id);
                cases.refetch();
              }}
              onRestore={async (id) => {
                await api.unarchiveCase(id);
                cases.refetch();
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
