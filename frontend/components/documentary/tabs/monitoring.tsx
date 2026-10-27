"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, CircleDot, Info, Pause, Play } from "lucide-react";
import { ApiError, api, monitoringApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import type { MonitoringAction, MonitoringIssue, MonitoringJob, MonitoringSnapshot } from "@/lib/types";
import { stageLabel } from "../shared";

const STATE_TEXT: Record<MonitoringSnapshot["state"], { label: string; variant: "success" | "warning" | "danger" | "info" }> = {
  problem: { label: "Something is wrong", variant: "danger" },
  attention: { label: "Waiting for you", variant: "warning" },
  running: { label: "Running", variant: "info" },
  idle: { label: "Nothing running", variant: "success" },
};

const SEVERITY = {
  problem: { icon: AlertTriangle, tone: "border-rose-500/40 bg-rose-500/10", badge: "danger" as const, label: "Problem" },
  attention: { icon: CircleDot, tone: "border-amber-500/40 bg-amber-500/10", badge: "warning" as const, label: "Needs you" },
  info: { icon: Info, tone: "border-border bg-muted/40", badge: "default" as const, label: "Note" },
};

const STAGE_DOT: Record<string, string> = {
  done: "bg-emerald-500",
  skipped: "bg-emerald-500/50",
  degraded: "bg-amber-500",
  running: "bg-sky-500 animate-pulse",
  failed: "bg-rose-500",
  blocked: "bg-rose-500",
  pending: "bg-muted-foreground/30",
};

function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "-";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export function MonitoringTab({
  caseId,
  onOpenTab,
  onChanged,
}: {
  caseId: number;
  onOpenTab: (tab: string) => void;
  onChanged: () => void;
}) {
  const router = useRouter();
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(true);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, error, loading } = useApi(() => monitoringApi.snapshot(caseId), [caseId, tick], { keepPrevious: true });
  const refreshMs = (data?.refresh_seconds ?? 5) * 1000;

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => clearInterval(timer);
  }, [live, refreshMs]);

  if (error && !data) return <ErrorState message={error} onRetry={() => setTick((t) => t + 1)} />;
  if (!data) return <Skeleton className="h-96 w-full" />;

  async function act(action: MonitoringAction) {
    if (action.kind === "tab") return onOpenTab(action.target);
    if (action.kind === "href") return router.push(action.target);
    if (action.kind === "cancel" && !window.confirm(`Cancel job #${action.target}? Finished stages are kept.`)) return;
    setBusy(true);
    try {
      if (action.kind === "resume") await api.resumeDocumentaryJob(Number(action.target));
      else await api.cancelDocumentaryJob(Number(action.target));
      setNote(`${action.kind === "resume" ? "Resumed" : "Cancelling"} job #${action.target}.`);
      onChanged();
      setTick((t) => t + 1);
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "The action failed.");
    } finally {
      setBusy(false);
    }
  }

  const status = STATE_TEXT[data.state];
  return (
    <div className="space-y-5" data-testid="monitoring-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Badge variant={status.variant} data-testid="monitoring-state">{status.label}</Badge>
          <span className="text-xs text-muted-foreground">
            Updated {formatDateTime(data.generated_at)}
            {live ? ` · every ${data.refresh_seconds} s` : " · paused"}
            {loading && " · refreshing…"}
          </span>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setLive((v) => !v)}>
          {live ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {live ? "Pause updates" : "Resume updates"}
        </Button>
      </div>
      {note && <p role="status" className="text-sm text-emerald-700">{note}</p>}
      {error && <p className="text-sm text-rose-700">Could not refresh: {error}</p>}

      <Issues issues={data.issues} onAct={act} busy={busy} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Running title="Running now" jobs={data.running} empty="No job is running." onOpenTab={onOpenTab} />
        <Running title="Waiting in the queue" jobs={data.queued} empty="The queue is empty." onOpenTab={onOpenTab} />
      </div>

      {data.latest_job && <Pipeline job={data.latest_job} onOpenTab={onOpenTab} />}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Services</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm" data-testid="monitoring-services">
            {data.services.map((s) => (
              <div key={s.name} className="flex items-start justify-between gap-3">
                <span>{s.name}</span>
                <span className="flex items-center gap-2 text-right text-xs text-muted-foreground">
                  {s.detail}
                  <Badge variant={s.state === "ok" ? "success" : s.state === "idle" ? "default" : "danger"}>{s.state}</Badge>
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Cost of this case</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm" data-testid="monitoring-budget">
            <p className="tabular-nums">EUR {data.budget.spent_eur.toFixed(2)} of {data.budget.cap_eur.toFixed(0)} spent</p>
            <Progress value={data.budget.used_fraction * 100} barClassName={data.budget.used_fraction >= 0.8 ? "bg-amber-500" : undefined} />
            <p className="text-xs text-muted-foreground">
              EUR {data.budget.remaining_eur.toFixed(2)} left · status {data.budget.status}
            </p>
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => router.push(`/cases/${caseId}`)}>
              Open the case
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Outside services, last {data.providers.window_minutes} min</CardTitle></CardHeader>
          <CardContent className="space-y-1.5 text-sm" data-testid="monitoring-providers">
            {data.providers.hosts.length === 0 && <p className="text-xs text-muted-foreground">No calls in this window.</p>}
            {data.providers.hosts.map((h) => (
              <div key={h.host} className="flex items-center justify-between gap-2">
                <span className="truncate">{h.host}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {h.calls} calls · {h.avg_ms} ms ·{" "}
                  <span className={h.errors ? "text-rose-600" : ""}>{h.errors} failed</span>
                </span>
              </div>
            ))}
            {data.providers.recent_errors.length > 0 && (
              <details className="pt-2 text-xs text-muted-foreground">
                <summary className="cursor-pointer">Latest failed calls ({data.providers.recent_errors.length})</summary>
                <ul className="mt-1 space-y-0.5">
                  {data.providers.recent_errors.map((e, i) => (
                    <li key={i}>{formatDateTime(e.at)} · {e.host}{e.path} · {e.status ?? "no answer"}</li>
                  ))}
                </ul>
              </details>
            )}
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => router.push("/ops")}>
              Open Operations
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Issues({ issues, onAct, busy }: { issues: MonitoringIssue[]; onAct: (a: MonitoringAction) => void; busy: boolean }) {
  if (issues.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm" data-testid="monitoring-clear">
        <CheckCircle2 className="size-4 text-emerald-600" /> Nothing needs your attention.
      </div>
    );
  }
  return (
    <section className="space-y-2" data-testid="monitoring-issues" aria-label="What needs you">
      {issues.map((issue, i) => {
        const look = SEVERITY[issue.severity];
        const Icon = look.icon;
        return (
          <div key={i} className={`rounded-md border px-4 py-3 ${look.tone}`} data-testid="monitoring-issue">
            <div className="flex items-start gap-3">
              <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={look.badge}>{look.label}</Badge>
                  <span className="text-sm font-medium">{issue.title}</span>
                </div>
                <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">{issue.detail}</p>
                {issue.actions.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {issue.actions.map((a) => (
                      <Button key={a.label} size="sm" variant={a.kind === "cancel" ? "secondary" : "default"}
                              disabled={busy} onClick={() => onAct(a)}>
                        {a.label}
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function Running({ title, jobs, empty, onOpenTab }: {
  title: string; jobs: MonitoringJob[]; empty: string; onOpenTab: (tab: string) => void;
}) {
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent className="space-y-4 text-sm">
        {jobs.length === 0 && <p className="text-xs text-muted-foreground">{empty}</p>}
        {jobs.map((job) => (
          <div key={job.id} className="space-y-1.5" data-testid="monitoring-job">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">
                Job #{job.id} · {job.case_title ?? `case ${job.case_id}`}
              </span>
              <Badge variant={job.status === "running" ? "info" : "default"}>{job.status}</Badge>
            </div>
            <Progress value={job.progress * 100} />
            <p className="text-xs text-muted-foreground">
              {Math.round(job.progress * 100)}% · {job.stage ? `now ${stageLabel(job.stage)}` : "starting"} ·
              started {duration(job.age_s)} ago
              {job.heartbeat_age_s != null && ` · worker heard ${duration(job.heartbeat_age_s)} ago`}
            </p>
            {job.current_call && (
              <p className="text-xs text-muted-foreground">
                Call #{job.current_call.call_number} to {job.current_call.provider}, {duration(job.current_call.elapsed_seconds)} so far
              </p>
            )}
            {job.stage && (
              <Button variant="link" size="sm" className="h-auto p-0"
                      onClick={() => onOpenTab(job.stages.find((s) => s.name === job.stage)?.tab ?? "Run")}>
                Open this stage
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function Pipeline({ job, onOpenTab }: { job: MonitoringJob; onOpenTab: (tab: string) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Pipeline of job #{job.id} · {job.status}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-muted-foreground">
          {Object.entries(job.stage_counts).map(([k, v]) => `${v} ${k}`).join(" · ")}. Click a stage to open its tab.
        </p>
        <ol className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3" data-testid="monitoring-stages">
          {job.stages.map((s) => (
            <li key={s.name}>
              <button type="button" onClick={() => onOpenTab(s.tab)}
                      className="flex w-full cursor-pointer items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-left text-xs hover:bg-muted/50"
                      title={s.error ?? `${s.status}, ${s.attempts} attempt(s)`}>
                <span className={`size-2.5 shrink-0 rounded-full ${STAGE_DOT[s.status] ?? "bg-muted-foreground/30"}`} aria-hidden />
                <span className="min-w-0 flex-1 truncate">{stageLabel(s.name)}</span>
                <span className="tabular-nums text-muted-foreground">{s.status === "pending" ? "" : duration(s.seconds)}</span>
                {s.attempts > 1 && <Badge variant="warning">x{s.attempts}</Badge>}
              </button>
            </li>
          ))}
        </ol>
        {job.warnings.length > 0 && (
          <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {job.warnings.map((w, i) => (
              <li key={i}>{w.stage ? `${w.stage}: ` : ""}{w.message}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
