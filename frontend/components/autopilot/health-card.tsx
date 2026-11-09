"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, autopilotApi, type AutopilotHealth } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { humanize } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const POLL_MS = 10_000;
const DOT: Record<AutopilotHealth["light"], string> = {
  green: "bg-emerald-500", amber: "bg-amber-400", red: "bg-red-500", grey: "bg-muted-foreground/30",
};

/** Home monitoring of the running Autopilot: one light per phase, the reason when it is red, and a retry button for it. */
export function AutopilotHealthCard() {
  const { data, refetch } = useApi(() => autopilotApi.runs(), [], { keepPrevious: true });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const timer = setInterval(refetch, POLL_MS);
    return () => clearInterval(timer);
  }, [refetch]);

  const active = (data?.runs ?? []).filter((r) => r.status === "running" || r.status === "waiting_owner");
  if (active.length === 0) return null;

  async function retry(runId: number, phase: string) {
    setBusy(`${runId}:${phase}`);
    setError(null);
    try {
      await autopilotApi.retryPhase(runId, phase);
      refetch();
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card data-testid="autopilot-health">
      <CardHeader>
        <CardTitle>Autopilot</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
        {active.map((run) => (
          <div key={run.id} data-testid={`autopilot-run-${run.id}`} className="space-y-2">
            <Link href={`/autopilot/${run.id}`} className="text-sm font-medium hover:underline">{run.case.title}</Link>
            <ul className="space-y-1">
              {run.health.map((h) => (
                <li key={h.phase} className="flex flex-wrap items-center gap-2 text-sm" data-testid={`autopilot-phase-${run.id}-${h.phase}`}>
                  <span className={`h-2.5 w-2.5 rounded-full ${DOT[h.light]}`} data-light={h.light} aria-label={h.light} />
                  <span className="w-20">{humanize(h.phase)}</span>
                  {h.reason && <span className="text-xs text-muted-foreground">{h.reason}</span>}
                  {h.can_retry && (
                    <Button size="sm" variant="secondary" disabled={busy === `${run.id}:${h.phase}`}
                            onClick={() => retry(run.id, h.phase)}>Retry</Button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
