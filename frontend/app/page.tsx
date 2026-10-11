"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  BookOpen,
  Coins,
  FileText,
  FlaskConical,
  FolderOpen,
  Link2,
  Timer,
} from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, formatDuration, formatUsd, langLabel } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { PageHeader } from "@/components/page-header";
import {
  CaseStatusBadge,
  RESOLUTION_STATUSES,
  ResolutionBadge,
  RunStatusBadge,
  resolutionLabel,
} from "@/components/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { FollowUpCard } from "@/components/lifecycle/follow-up-card";
import { MonitorStatusCard } from "@/components/lifecycle/monitor-status";
import { BackupCard } from "@/components/backup-card";
import type { DocumentaryJob, FollowUpCandidate, ResolutionStatus, VideoCostSummary } from "@/lib/types";

const KNOWN_AGENTS = [
  "Discovery Agent",
  "Research Agent",
  "Fact Extractor",
  "Evidence Normalizer",
  "Story Director",
  "Writer",
  "Engagement Critic",
  "Grounding Validator",
  "Consistency Checker",
  "Section Critic",
  "Similarity Critic",
];

export default function DashboardPage() {
  const [tick, setTick] = useState(0);
  // keepPrevious: refreshing after a follow-up decision or a monitor run
  // keeps the dashboard on screen.
  const { data, error, loading, refetch } = useApi(() => api.dashboard(), [tick], { keepPrevious: true });
  const costs = useApi(() => api.videoCostSummary(), [tick], { keepPrevious: true });
  const [started, setStarted] = useState<{ fu: FollowUpCandidate; job: DocumentaryJob } | null>(null);
  const refresh = () => setTick((t) => t + 1);
  const followUps = data?.follow_up_candidates ?? [];

  return (
    <div>
      <PageHeader title="Dashboard" description="Research and writing activity across the studio." />

      {loading && !data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-20" />
            ))}
          </div>
          <TableSkeleton rows={6} cols={6} />
        </div>
      )}

      {error && !data && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="space-y-6">
          {error && (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/5 px-4 py-2 text-xs text-rose-600 dark:text-rose-400">
              Could not refresh the dashboard: {error}
            </div>
          )}

          {started && (
            <div
              role="status"
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-4 py-3 text-sm"
            >
              <span>
                Update video started for{" "}
                <span className="font-medium">{started.fu.case_title ?? `case #${started.fu.case_id}`}</span> — follow-up
                job #{started.job.id} ({started.job.mode}, {started.job.languages.map((l) => l.toUpperCase()).join(" ")}).
              </span>
              <span className="flex items-center gap-3">
                <Link href={`/documentary/${started.fu.case_id}`} className="text-xs font-medium text-primary hover:underline">
                  Follow the production
                </Link>
                <button
                  type="button"
                  onClick={() => setStarted(null)}
                  className="cursor-pointer text-xs text-muted-foreground hover:text-foreground"
                >
                  Dismiss
                </button>
              </span>
            </div>
          )}

          {followUps.length > 0 && (
            <section aria-label="Follow-up decisions" className="space-y-3">
              {followUps.map((fu) => (
                <FollowUpCard
                  key={fu.id}
                  candidate={fu}
                  onApproved={(f, job) => {
                    setStarted({ fu: f, job });
                    refresh();
                  }}
                  onDismissed={refresh}
                />
              ))}
            </section>
          )}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat icon={FolderOpen} label="Total Cases" value={data.stats.total_cases} />
            <Stat icon={FlaskConical} label="Researched" value={data.stats.cases_researched} />
            <Stat icon={BookOpen} label="Stories Completed" value={data.stats.stories_completed} />
            <Stat icon={Timer} label="Waiting for Research" value={data.stats.cases_waiting} />
            <Stat icon={Link2} label="Sources Collected" value={data.stats.sources_collected} />
          </div>

          <ArchivedVideoCosts state={costs} />

          {data.resolution_counts && <ResolutionTiles counts={data.resolution_counts} />}

          <MonitorStatusCard onRunFinished={refresh} />
          <BackupCard />

          <Card>
            <CardHeader>
              <CardTitle>Recent Cases</CardTitle>
              <Link href="/cases" className="text-xs text-primary hover:underline">
                View all
              </Link>
            </CardHeader>
            <CardContent className="p-0">
              {data.recent_cases.length === 0 ? (
                <div className="p-4">
                  <EmptyState
                    title="No cases yet"
                    description="Run discovery to find new true-crime cases to investigate."
                    action={
                      <Link href="/discover">
                        <span className="text-sm text-primary hover:underline">Open Discover</span>
                      </Link>
                    }
                  />
                </div>
              ) : (
                <Table>
                  <THead>
                    <TR className="hover:bg-transparent">
                      <TH>Case</TH>
                      <TH>Status</TH>
                      <TH className="text-right">Sources</TH>
                      <TH className="text-right">Facts</TH>
                      <TH className="text-right">Contradictions</TH>
                      <TH className="text-right">Latest Version</TH>
                      <TH className="text-right">Engagement</TH>
                      <TH>Created</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {data.recent_cases.map((c) => (
                      <TR key={c.id} className="cursor-pointer">
                        <TD>
                          <span className="flex flex-wrap items-center gap-2">
                            <Link href={`/cases/${c.id}`} className="font-medium hover:text-primary">
                              {c.title}
                            </Link>
                            <ResolutionBadge status={c.resolution_status} />
                          </span>
                        </TD>
                        <TD>
                          <CaseStatusBadge status={c.status} />
                        </TD>
                        <TD className="text-right tabular-nums">{c.sources}</TD>
                        <TD className="text-right tabular-nums">{c.facts}</TD>
                        <TD className="text-right tabular-nums">{c.contradictions}</TD>
                        <TD className="text-right tabular-nums">
                          {c.latest_story_version ? `v${c.latest_story_version}` : "—"}
                        </TD>
                        <TD className="text-right tabular-nums">
                          {c.engagement_score != null ? Math.round(c.engagement_score) : "—"}
                        </TD>
                        <TD className="text-muted-foreground">{formatDate(c.created_at)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Agent Activity</CardTitle>
              <Link href="/research" className="text-xs text-primary hover:underline">
                All runs
              </Link>
            </CardHeader>
            <CardContent className="p-0">
              <div className="divide-y divide-border">
                {(() => {
                  const known = KNOWN_AGENTS.map((name) => ({
                    name,
                    run: data.agent_activity.find((a) => a.agent_name === name),
                  }));
                  const extra = data.agent_activity.filter(
                    (a) => !KNOWN_AGENTS.includes(a.agent_name),
                  );
                  const rows = [
                    ...known,
                    ...extra.map((r) => ({ name: r.agent_name, run: r })),
                  ];
                  return rows.map(({ name, run }) => (
                    <div key={name} className="flex items-center justify-between px-4 py-2.5">
                      <span className="flex items-center gap-2 text-sm">
                        <FileText className="size-3.5 text-muted-foreground" />
                        {name}
                      </span>
                      <span className="flex items-center gap-3">
                        {run?.duration_ms != null && (
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {formatDuration(run.duration_ms)}
                          </span>
                        )}
                        {run ? (
                          <RunStatusBadge status={run.status} />
                        ) : (
                          <RunStatusBadge status="waiting" />
                        )}
                      </span>
                    </div>
                  ));
                })()}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

function ArchivedVideoCosts({ state }: { state: ReturnType<typeof useApi<VideoCostSummary>> }) {
  const summary = state.data?.summary;
  const videos = state.data?.videos ?? [];
  return (
    <Card>
      <CardHeader className="flex-wrap gap-3">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Coins className="size-4 text-amber-500" aria-hidden />
            Archived video spend
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            Reported model cost across published and archived videos. Shared production work is counted once.
          </p>
        </div>
        <Link href="/archive" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
          Open archive <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent>
        {state.loading && !state.data ? (
          <Skeleton className="h-24" />
        ) : state.error && !state.data ? (
          <p className="text-sm text-muted-foreground">Could not load cost data: {state.error}</p>
        ) : !summary || summary.video_count === 0 ? (
          <p className="text-sm text-muted-foreground">No published or archived videos have cost records yet.</p>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <CostStat label="Total" value={formatUsd(summary.total_cost_usd)} tone="amber" />
              <CostStat label="Direct video work" value={formatUsd(summary.direct_cost_usd)} />
              <CostStat label="Shared production" value={formatUsd(summary.shared_cost_usd)} />
              <CostStat label="Videos" value={String(summary.video_count)} />
            </div>
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full min-w-[680px] text-sm">
                <thead className="border-b border-border bg-muted/30">
                  <tr>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Video</th>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Language</th>
                    <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Direct</th>
                    <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Shared allocation</th>
                    <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {videos.slice(0, 8).map((video) => (
                    <tr key={video.video_id} className="border-b border-border last:border-0 hover:bg-muted/40">
                      <td className="max-w-[260px] px-3 py-2.5">
                        <Link href={`/videos/${video.video_id}/analysis`} className="block truncate font-medium hover:text-primary">
                          {video.title || video.case_title || `Video #${video.video_id}`}
                        </Link>
                        <span className="text-xs text-muted-foreground">{video.case_title || `Case #${video.case_id}`}</span>
                      </td>
                      <td className="px-3 py-2.5 text-muted-foreground">{langLabel(video.language)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{formatUsd(video.direct_cost_usd)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{formatUsd(video.shared_allocation_usd)}</td>
                      <td className="px-3 py-2.5 text-right font-medium tabular-nums text-amber-600 dark:text-amber-400">
                        {formatUsd(video.fully_loaded_cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {summary.missing_cost_runs > 0 && (
              <p className="text-xs text-muted-foreground">
                {summary.missing_cost_runs} run{summary.missing_cost_runs === 1 ? "" : "s"} have token data but no provider-reported price.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CostStat({ label, value, tone }: { label: string; value: string; tone?: "amber" }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 px-3 py-2.5">
      <p className={`text-lg font-semibold tabular-nums ${tone === "amber" ? "text-amber-600 dark:text-amber-400" : ""}`}>{value}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

const RESOLUTION_TONE: Record<ResolutionStatus, string> = {
  SOLVED: "text-emerald-600 dark:text-emerald-400",
  UNSOLVED: "text-rose-600 dark:text-rose-400",
  STATUS_UNDER_REVIEW: "text-amber-600 dark:text-amber-400",
  UNKNOWN: "text-foreground",
};

/** How many cases are solved, unsolved, under review or unknown — each opens the filtered case list. */
function ResolutionTiles({ counts }: { counts: Partial<Record<ResolutionStatus, number>> }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {RESOLUTION_STATUSES.map((s) => (
        <Link
          key={s}
          href={`/cases?resolution=${s}`}
          className="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          aria-label={`${counts[s] ?? 0} ${resolutionLabel(s).toLowerCase()} cases`}
        >
          <Card className="h-full transition-colors hover:bg-muted/40">
            <CardContent className="flex items-center gap-3 p-4">
              <p className={`text-xl font-semibold tabular-nums leading-none ${RESOLUTION_TONE[s]}`}>{counts[s] ?? 0}</p>
              <div className="min-w-0">
                <ResolutionBadge status={s} />
                <p className="mt-1 text-xs text-muted-foreground">cases</p>
              </div>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
          <Icon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0">
          <p className="text-xl font-semibold tabular-nums leading-none">{value}</p>
          <p className="mt-1 truncate text-xs text-muted-foreground">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}
