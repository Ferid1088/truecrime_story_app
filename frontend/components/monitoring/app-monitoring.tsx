"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pause, Play } from "lucide-react";
import { monitoringApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import { PageHeader } from "@/components/page-header";
import {
  Issues,
  ProvidersCard,
  Running,
  ServicesCard,
  STATE_TEXT,
  useMonitoringActions,
} from "@/components/documentary/monitoring-parts";

export function AppMonitoring() {
  const router = useRouter();
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(true);
  const { data, error, loading } = useApi(() => monitoringApi.app(), [tick], { keepPrevious: true });
  const { act, busy, note } = useMonitoringActions({ onChanged: () => setTick((t) => t + 1) });
  const refreshMs = (data?.refresh_seconds ?? 5) * 1000;

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => clearInterval(timer);
  }, [live, refreshMs]);

  if (error && !data) return <ErrorState message={error} onRetry={() => setTick((t) => t + 1)} />;
  if (!data) return <Skeleton className="h-96 w-full" />;

  const status = STATE_TEXT[data.state];
  const openStage = (tab: string, caseId: number) => router.push(`/documentary/${caseId}?tab=${encodeURIComponent(tab)}`);
  return (
    <div data-testid="app-monitoring">
      <PageHeader
        title="Monitoring"
        description="What is running, what waits and what needs you, across the whole app."
        actions={
          <Button variant="secondary" size="sm" onClick={() => setLive((v) => !v)}>
            {live ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {live ? "Pause updates" : "Resume updates"}
          </Button>
        }
      />
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <Badge variant={status.variant} data-testid="monitoring-state">{status.label}</Badge>
          <span className="text-xs text-muted-foreground">
            Updated {formatDateTime(data.generated_at)}
            {live ? ` · every ${data.refresh_seconds} s` : " · paused"}
            {loading && " · refreshing…"}
          </span>
        </div>
        {note && <p role="status" className="text-sm text-emerald-700">{note}</p>}
        {error && <p className="text-sm text-rose-700">Could not refresh: {error}</p>}

        <Issues issues={data.issues} onAct={act} busy={busy} />

        <div className="grid gap-5 lg:grid-cols-2">
          <Running title="Running now" jobs={data.running} empty="No job is running." onOpenTab={openStage} />
          <Running title="Waiting in the queue" jobs={data.queued} empty="The queue is empty." onOpenTab={openStage} />
        </div>

        <Card>
          <CardHeader><CardTitle>Documentaries that run or stopped lately</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm" data-testid="monitoring-cases">
            {data.cases.length === 0 && <p className="text-xs text-muted-foreground">Nothing is running and nothing stopped lately.</p>}
            {data.cases.map((c) => {
              const budget = data.budgets.find((b) => b.case_id === c.id);
              return (
                <div key={c.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3 last:border-0 last:pb-0">
                  <div className="min-w-0">
                    <Link href={`/documentary/${c.id}?tab=Monitoring`} className="font-medium hover:underline">
                      {c.title}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {c.job ? `Job #${c.job.id} · ${c.job.status} · ${c.job.stage ?? "no stage"}` : "No recent job"}
                    </p>
                  </div>
                  {budget && (
                    <div className="w-48 space-y-1">
                      <Progress value={budget.used_fraction * 100} barClassName={budget.used_fraction >= 0.8 ? "bg-amber-500" : undefined} />
                      <p className="text-xs tabular-nums text-muted-foreground">
                        EUR {budget.spent_eur.toFixed(2)} of {budget.cap_eur.toFixed(0)}
                      </p>
                    </div>
                  )}
                  <Button size="sm" variant="secondary" onClick={() => act({ label: "", kind: "href", target: `/documentary/${c.id}?tab=Monitoring` })}>
                    Open monitoring
                  </Button>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <div className="grid gap-5 lg:grid-cols-2">
          <ServicesCard services={data.services} />
          <ProvidersCard providers={data.providers} />
        </div>
      </div>
    </div>
  );
}
