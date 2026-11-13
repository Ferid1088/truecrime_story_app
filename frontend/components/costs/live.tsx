"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Radio } from "lucide-react";
import { api } from "@/lib/api";
import type { CostCall, CostLive } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CallTable, eur } from "./shared";

const POLL_MS = 3000;
const KEEP = 40;

/** The newest paid calls as they happen, with the money per minute; asks for what is new every few seconds. Mount it with the case as its key. */
export function LiveFeed({ caseId }: { caseId: number | null }) {
  const [calls, setCalls] = useState<CostCall[]>([]);
  const [stats, setStats] = useState<CostLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const last = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const fresh = await api.costsLive(caseId, last.current);
      setError(null);
      setStats(fresh);
      if (fresh.last_id != null) last.current = fresh.last_id;
      if (fresh.calls.length) setCalls((old) => [...fresh.calls, ...old].slice(0, KEEP));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the live feed");
    }
  }, [caseId]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(load, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  return (
    <Card data-testid="cost-live">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2"><Radio className="size-4 text-emerald-500" aria-hidden /> Live</CardTitle>
        {stats && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {eur(stats.eur_per_minute, 3)}/min · last 5 min {eur(stats.last_5_min_eur)} · last hour {eur(stats.last_hour_eur)}
          </span>
        )}
      </CardHeader>
      <CardContent>
        {error && <p className="text-xs text-destructive">{error}</p>}
        {calls.length === 0 && !error ? <p className="text-xs text-muted-foreground">Waiting for the next paid call…</p> : <div className="max-h-96 overflow-y-auto"><CallTable calls={calls} /></div>}
      </CardContent>
    </Card>
  );
}
