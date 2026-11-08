"use client";

import Link from "next/link";
import { useEffect } from "react";
import { orchestratorApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";

const POLL_MS = 5000;
const gb = (n: number) => `${n.toFixed(1)} GB`;

export default function OrchestratorPage() {
  const { data, refetch } = useApi(() => orchestratorApi.view(), [], { keepPrevious: true });
  useEffect(() => {
    const timer = setInterval(refetch, POLL_MS);
    return () => clearInterval(timer);
  }, [refetch]);
  if (!data) return <p className="text-sm text-muted-foreground">Loading the orchestrator…</p>;
  const host = data.host;
  const limits = data.host_limits;
  const memoryLow = host && limits ? host.free_gb - limits.job_memory_gb < limits.min_free_memory_gb : false;
  const loadHigh = host && limits ? host.load_per_core > limits.max_load_per_core : false;
  return (
    <div className="space-y-4" data-testid="orchestrator">
      <PageHeader
        title="Orchestrator"
        description="Watches the own machine and every provider, starts a film only when both have room, and keeps the rest queued."
      />
      {!data.worker_alive && (
        <p role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm" data-testid="orchestrator-no-worker">
          No worker has reported in the last minute: nothing is being started.
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>This machine</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm" data-testid="orchestrator-host">
            {host && limits ? (
              <>
                <div className="flex justify-between">
                  <span>Free memory</span>
                  <span className={memoryLow ? "text-amber-600" : ""}>{gb(host.free_gb)} of {gb(host.total_gb)}</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  A film needs about {gb(limits.job_memory_gb)}; {gb(limits.min_free_memory_gb)} must stay free.
                </p>
                <div className="flex justify-between">
                  <span>Load per core ({host.cores} cores)</span>
                  <span className={loadHigh ? "text-amber-600" : ""}>{host.load_per_core.toFixed(2)} (limit {limits.max_load_per_core.toFixed(2)})</span>
                </div>
              </>
            ) : <p className="text-muted-foreground">No reading yet.</p>}
            <div className="flex justify-between border-t border-border pt-2">
              <span>Films running</span>
              <span>{data.running} of {data.cap}</span>
            </div>
            {data.decision && (
              <p className="text-xs" data-testid="orchestrator-decision">
                <Badge variant={data.decision.allowed ? "success" : "warning"}>{data.decision.allowed ? "room for more" : humanize(data.decision.code)}</Badge>{" "}
                {data.decision.reason}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Providers</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm" data-testid="orchestrator-providers">
            {Object.entries(data.providers).map(([name, p]) => (
              <div key={name} className="flex items-center justify-between gap-2">
                <span>{humanize(name)}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  {p.active} running · limit {p.limit} of {p.configured}
                  {p.throttled && <Badge variant="warning" data-testid={`orchestrator-throttled-${name}`}>throttled</Badge>}
                </span>
              </div>
            ))}
            <p className="pt-1 text-xs text-muted-foreground">
              A provider that answers “too many requests” gets half its limit, then one slot back every half minute.
            </p>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>Films</CardTitle></CardHeader>
        <CardContent className="space-y-1 text-sm" data-testid="orchestrator-jobs">
          {data.jobs.length === 0 && <p className="text-muted-foreground">No film is running or waiting.</p>}
          {data.jobs.map((j) => (
            <div key={j.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 py-1 last:border-0">
              <Link href={`/cases/${j.case_id}`} className="font-medium hover:underline">{j.case_title ?? `Case ${j.case_id}`}</Link>
              <span className="text-xs text-muted-foreground">
                {j.languages.join(", ")} · {humanize(j.status)}{j.stage ? ` · ${humanize(j.stage)}` : ""}
                {j.waiting_because ? ` · ${j.waiting_because}` : ""}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Autopilot runs</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm" data-testid="orchestrator-runs">
          {data.runs.length === 0 && <p className="text-muted-foreground">No Autopilot run is going.</p>}
          {data.runs.map((r) => (
            <div key={r.id} className="space-y-1 rounded-md border border-border p-2">
              <div className="flex items-center justify-between gap-2">
                <Link href={`/autopilot/${r.id}`} className="font-medium hover:underline">{r.case.title}</Link>
                <Badge variant="outline">{humanize(r.status)}{r.stage ? ` · ${humanize(r.stage)}` : ""}</Badge>
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs">
                {Object.entries(r.languages).map(([lang, status]) => (
                  <Badge key={lang} variant="outline">{lang}: {status ?? "not started"}</Badge>
                ))}
              </div>
              {r.needs_you.map((n) => <p key={n.key} className="text-xs text-amber-700">{n.language ? `${n.language}: ` : ""}{n.message}</p>)}
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>What changed</CardTitle></CardHeader>
        <CardContent className="space-y-1 text-xs" data-testid="orchestrator-events">
          {data.events.length === 0 && <p className="text-muted-foreground">Nothing yet.</p>}
          {data.events.map((e) => (
            <p key={`${e.at}-${e.code}`}><span className="text-muted-foreground">{formatDateTime(new Date(e.at * 1000).toISOString())}</span> · {humanize(e.code)}: {e.reason}</p>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
