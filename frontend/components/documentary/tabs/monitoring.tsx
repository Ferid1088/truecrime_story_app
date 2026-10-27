"use client";

import { useEffect, useState } from "react";
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
import { Issues, Pipeline, ProvidersCard, Running, ServicesCard, STATE_TEXT, useMonitoringActions } from "../monitoring-parts";

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
  const { data, error, loading } = useApi(() => monitoringApi.snapshot(caseId), [caseId, tick], { keepPrevious: true });
  const { act, busy, note } = useMonitoringActions({ onOpenTab, onChanged: () => { onChanged(); setTick((t) => t + 1); } });
  const refreshMs = (data?.refresh_seconds ?? 5) * 1000;

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => clearInterval(timer);
  }, [live, refreshMs]);

  if (error && !data) return <ErrorState message={error} onRetry={() => setTick((t) => t + 1)} />;
  if (!data) return <Skeleton className="h-96 w-full" />;

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
        <ServicesCard services={data.services} />
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
        <ProvidersCard providers={data.providers} />
      </div>
    </div>
  );
}

