"use client";

import Link from "next/link";
import { ArrowLeft, ExternalLink, Layers3, ListFilter, Wallet } from "lucide-react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDuration, formatUsd, humanize, langLabel } from "@/lib/format";
import type { VideoCostDetail, VideoCostRun } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { FilmStateBadge } from "@/components/lifecycle/shared";

export default function VideoAnalysisPage() {
  const params = useParams<{ id: string }>();
  const videoId = Number(params.id);
  const state = useApi(() => api.videoCostDetail(videoId), [videoId], { keepPrevious: true });

  if (state.loading && !state.data) {
    return <div className="space-y-4"><Skeleton className="h-16" /><Skeleton className="h-48" /><Skeleton className="h-96" /></div>;
  }
  if (state.error && !state.data) return <ErrorState message={state.error} onRetry={state.refetch} />;
  if (!state.data) return <EmptyState title="Video not found" description="This video does not have a cost analysis record." />;

  return <VideoAnalysis detail={state.data} />;
}

function VideoAnalysis({ detail }: { detail: VideoCostDetail }) {
  const { video, job, summary } = detail;
  const runs = [...detail.runs].sort((a, b) => {
    const costA = a.estimated_cost_usd ?? -1;
    const costB = b.estimated_cost_usd ?? -1;
    return costB - costA || (b.total_tokens ?? 0) - (a.total_tokens ?? 0) || b.id - a.id;
  });
  const title = video.title || video.case_title || `Video #${video.id}`;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Video cost analysis"
        description={`${title} · ${langLabel(video.language)} · a complete model and pipeline cost ledger.`}
        actions={
          <Link href="/archive" className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline">
            <ArrowLeft className="size-3.5" aria-hidden /> Back to archive
          </Link>
        }
      />

      <Card className="border-amber-500/20 bg-amber-500/[0.03]">
        <CardContent className="flex flex-wrap items-start justify-between gap-4 p-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold">{title}</h2>
              <FilmStateBadge state={video.state} />
              <Badge variant="outline">{langLabel(video.language)}</Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Case: {video.case_title || `#${video.case_id}`} · Video #{video.id}
              {job ? ` · Job #${job.id} · ${job.mode} run` : " · no documentary job attribution"}
            </p>
          </div>
          {video.youtube_url && (
            <a href={video.youtube_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
              Watch published video <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Metric icon={Wallet} label="Direct language cost" value={formatUsd(summary.direct.cost_usd)} detail={`${summary.direct.run_count} runs`} />
        <Metric icon={Layers3} label="Shared allocation" value={formatUsd(summary.shared_allocation_usd)} detail={`${summary.shared.run_count} shared runs · ${summary.output_count} outputs`} />
        <Metric icon={ListFilter} label="Fully loaded estimate" value={formatUsd(summary.fully_loaded_cost_usd)} detail="Direct + allocated shared work" accent />
      </div>

      {detail.outputs.length > 1 && (
        <Card>
          <CardHeader>
            <CardTitle>Other language outputs from this job</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {detail.outputs.map((output) => (
              <Link
                key={output.video_id}
                href={`/videos/${output.video_id}/analysis`}
                className={`rounded-md border px-3 py-2 transition-colors hover:bg-muted/50 ${output.video_id === video.id ? "border-primary/50 bg-accent/40" : "border-border"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{langLabel(output.language)}</span>
                  <span className="text-xs tabular-nums text-amber-600 dark:text-amber-400">{formatUsd(output.fully_loaded_cost_usd)}</span>
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">{output.title || `Video #${output.video_id}`}</p>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex-wrap gap-2">
          <div>
            <CardTitle>Cost ledger</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">Sorted by reported cost, highest first. Shared stages are clearly marked.</p>
          </div>
          <Badge variant="outline">{runs.length} model runs</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {runs.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="No attributed model runs"
                description="This video was created before documentary cost attribution was enabled, or its provider did not record a model run. New documentary jobs will populate this ledger."
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[960px] text-sm">
                <thead className="border-b border-border bg-muted/30">
                  <tr>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Cost</th>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Part</th>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Agent / role</th>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Provider / model</th>
                    <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Tokens</th>
                    <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Duration</th>
                    <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => <RunRow key={run.id} run={run} />)}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {summary.direct.missing_cost_runs + summary.shared.missing_cost_runs > 0 && (
        <p className="text-xs text-muted-foreground">
          {summary.direct.missing_cost_runs + summary.shared.missing_cost_runs} run{summary.direct.missing_cost_runs + summary.shared.missing_cost_runs === 1 ? "" : "s"} returned token usage without a provider-reported dollar price; they remain visible but are excluded from dollar totals.
        </p>
      )}
    </div>
  );
}

function Metric({ icon: Icon, label, value, detail, accent }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string; detail: string; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground"><Icon className="size-3.5" aria-hidden /> {label}</div>
        <p className={`mt-2 text-xl font-semibold tabular-nums ${accent ? "text-amber-600 dark:text-amber-400" : ""}`}>{value}</p>
        <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: VideoCostRun }) {
  const stage = humanize(run.pipeline_stage || "unclassified");
  const model = run.model || "Model not reported";
  return (
    <tr className="border-b border-border last:border-0 hover:bg-muted/40">
      <td className="px-3 py-3 align-top">
        <span className="font-semibold tabular-nums text-amber-600 dark:text-amber-400">{formatUsd(run.estimated_cost_usd)}</span>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Run #{run.id}</p>
      </td>
      <td className="px-3 py-3 align-top">
        <Badge variant={run.scope === "shared" ? "accent" : "info"}>{run.scope === "shared" ? "Shared" : "Video"}</Badge>
        <p className="mt-1 max-w-[150px] text-xs capitalize text-muted-foreground">{stage}</p>
      </td>
      <td className="max-w-[210px] px-3 py-3 align-top">
        <p className="font-medium">{run.agent_name}</p>
        {run.role && <p className="mt-0.5 text-xs text-muted-foreground">{humanize(run.role)}</p>}
        {(run.input_summary || run.output_summary) && (
          <details className="mt-1 text-xs text-muted-foreground">
            <summary className="cursor-pointer hover:text-foreground">Show trace</summary>
            <div className="mt-1 max-w-[240px] space-y-1 whitespace-pre-wrap break-words rounded bg-muted/30 p-2">
              {run.input_summary && <p><span className="font-medium text-foreground">Input:</span> {run.input_summary}</p>}
              {run.output_summary && <p><span className="font-medium text-foreground">Output:</span> {run.output_summary}</p>}
            </div>
          </details>
        )}
      </td>
      <td className="max-w-[220px] px-3 py-3 align-top">
        <p className="truncate font-medium" title={model}>{model}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{run.provider || "Provider not reported"}</p>
      </td>
      <td className="px-3 py-3 text-right align-top tabular-nums">
        <p>{run.total_tokens?.toLocaleString() ?? "—"}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">in {run.input_tokens?.toLocaleString() ?? "—"} · out {run.output_tokens?.toLocaleString() ?? "—"}</p>
      </td>
      <td className="px-3 py-3 text-right align-top tabular-nums text-muted-foreground">{formatDuration(run.duration_ms)}</td>
      <td className="px-3 py-3 align-top"><Badge variant={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : "warning"}>{run.status}</Badge></td>
    </tr>
  );
}
