"use client";

import Link from "next/link";
import { Gauge } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import type { ConcurrencyLimits, DocumentaryJob, DocumentaryScheduler, ResolutionStatus } from "@/lib/types";
import { ResolutionBadge } from "@/components/status-badge";
import { ProductionTypeBadge } from "@/components/lifecycle/shared";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ErrorState } from "@/components/state";
import { cn } from "@/lib/utils";
import { InlineAlert, JobStatusBadge, jobActivity, jobProgressTone } from "./shared";

function JobLine({ job, title, position }: { job: DocumentaryJob; title: string; position?: number }) {
  return (
    <li className="px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <Link href={`/documentary/${job.case_id}`} className="min-w-0 truncate font-medium text-primary hover:underline">
          {position != null && <span className="mr-1 text-muted-foreground">{position}.</span>}
          {title}
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          <ProductionTypeBadge type={job.production_type} />
          <span className="tabular-nums text-muted-foreground">{Math.round(job.progress * 100)}%</span>
          <JobStatusBadge status={job.status} />
        </span>
      </div>
      {job.status !== "queued" && (
        <Progress value={job.progress * 100} className="mt-1.5 h-1" barClassName={jobProgressTone(job.status)} />
      )}
      <p className="mt-1 break-words text-muted-foreground">
        {job.status === "queued" ? `Job #${job.id} · waiting for a free slot` : jobActivity(job)}
      </p>
    </li>
  );
}

/** What runs now, what waits, and how busy the shared limits are. */
export function SchedulerCard({
  data,
  error,
  onRetry,
  titles,
  configured,
}: {
  data: DocumentaryScheduler | null;
  error: string | null;
  onRetry: () => void;
  titles: Map<number, string>;
  configured: ConcurrencyLimits;
}) {
  const title = (job: DocumentaryJob) => titles.get(job.case_id) ?? `Case #${job.case_id}`;
  const limits = Object.entries(data?.limits ?? {});

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4" /> Scheduler
        </CardTitle>
        {data && (
          <span className="text-xs tabular-nums text-muted-foreground">
            {data.running.length}/{data.max_parallel_jobs} running · {data.queued.length} queued
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {!data && error && <ErrorState message={error} onRetry={onRetry} />}
        {!data && !error && <Skeleton className="h-24" />}
        {data && error && <InlineAlert tone="warning">Could not refresh: {error}</InlineAlert>}
        {data && data.running.length + data.queued.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing is running. Started documentaries appear here.</p>
        )}
        {data && data.running.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Running</p>
            <ul className="divide-y divide-border rounded-md border border-border">
              {data.running.map((j) => (
                <JobLine key={j.id} job={j} title={title(j)} />
              ))}
            </ul>
          </div>
        )}
        {data && data.queued.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Queued</p>
            <ol className="divide-y divide-border rounded-md border border-border">
              {data.queued.map((j, i) => (
                <JobLine key={j.id} job={j} title={title(j)} position={i + 1} />
              ))}
            </ol>
          </div>
        )}
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Shared limits</p>
          {limits.length > 0 ? (
            <ul className="grid grid-cols-2 gap-x-4 gap-y-2">
              {limits.map(([name, { active, limit }]) => (
                <li key={name} className="text-[11px]">
                  <div className="mb-0.5 flex justify-between gap-2">
                    <span className="truncate text-muted-foreground">{humanize(name)}</span>
                    <span className="tabular-nums">
                      {active}/{limit}
                    </span>
                  </div>
                  <Progress
                    value={limit ? (active / limit) * 100 : 0}
                    className="h-1"
                    barClassName={cn(active >= limit && limit > 0 && "bg-amber-500")}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] leading-4 text-muted-foreground">
              No live usage reported. Configured:{" "}
              {Object.entries(configured)
                .map(([name, n]) => `${humanize(name)} ${n}`)
                .join(" · ")}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** Latest documentary jobs of every case. */
export function RecentJobs({
  refreshKey,
  resolutions,
}: {
  refreshKey: string;
  /** Case id → resolution status (job rows carry no case payload). */
  resolutions?: Map<number, ResolutionStatus>;
}) {
  const { data, error, loading, refetch } = useApi(() => api.listDocumentaryJobs(10), [refreshKey], {
    keepPrevious: true,
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent runs</CardTitle>
        <span className="text-xs text-muted-foreground">all cases</span>
      </CardHeader>
      <CardContent className="p-0">
        {!data && loading && (
          <div className="p-4">
            <Skeleton className="h-24" />
          </div>
        )}
        {!data && error && (
          <div className="p-4">
            <ErrorState message={error} onRetry={refetch} />
          </div>
        )}
        {data && data.length === 0 && <p className="px-4 py-6 text-sm text-muted-foreground">No runs yet.</p>}
        {data && data.length > 0 && (
          <Table className="text-xs">
            <THead>
              <TR className="hover:bg-transparent">
                <TH>Case</TH>
                <TH>Status</TH>
                <TH className="text-right">Progress</TH>
              </TR>
            </THead>
            <TBody>
              {data.map((j) => (
                <TR key={j.id}>
                  <TD className="max-w-44">
                    <Link
                      href={`/documentary/${j.case_id}`}
                      className="block truncate font-medium text-primary hover:underline"
                      title={j.case_title ?? undefined}
                    >
                      {j.case_title ?? `Case #${j.case_id}`}
                    </Link>
                    <span className="text-[10px] text-muted-foreground">
                      #{j.id} · {j.mode}
                      {j.from_zero && " · from zero"} · {formatDateTime(j.created_at)}
                    </span>
                    {(resolutions?.has(j.case_id) || j.production_type === "follow_up") && (
                      <span className="mt-1 flex flex-wrap items-center gap-1">
                        {resolutions?.has(j.case_id) && <ResolutionBadge status={resolutions.get(j.case_id)} />}
                        <ProductionTypeBadge type={j.production_type} />
                      </span>
                    )}
                  </TD>
                  <TD>
                    <JobStatusBadge status={j.status} />
                  </TD>
                  <TD className="text-right tabular-nums">{Math.round(j.progress * 100)}%</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
