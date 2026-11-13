"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { CostPhases } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { eur } from "./shared";

/** One colour per vendor (never per rank); a vendor without a slot of its own is gray. */
const VENDOR: Record<string, string> = {
  apimaster: "var(--viz-1)", heygen: "var(--viz-2)", elevenlabs: "var(--viz-3)", brave: "var(--viz-4)", apiko: "var(--viz-5)",
};
const colour = (key: string) => VENDOR[key] ?? "var(--viz-other)";

/** Where the money went, phase by phase in the order of the pipeline: one bar per phase, split by vendor (APIMaster, ElevenLabs, HeyGen…). */
export function PhaseVendors({ data }: { data: CostPhases }) {
  const [open, setOpen] = useState<string | null>(null);
  const max = Math.max(...data.steps.map((s) => s.total_eur), 0.0001);
  const used = data.vendors.filter((v) => data.steps.some((s) => s.vendors[v.key]));
  return (
    <div data-testid="phase-vendors">
      <ul className="space-y-1.5">
        {data.steps.map((s) => (
          <li key={s.key} className="relative" onPointerEnter={() => setOpen(s.key)} onPointerLeave={() => setOpen(null)} data-testid={`phase-${s.key}`}>
            <div className="grid grid-cols-[10rem_1fr_5rem] items-center gap-2 text-xs">
              <span className="truncate">{s.label}</span>
              <div className="flex h-4 gap-0.5" style={{ width: `${Math.max((s.total_eur / max) * 100, 1)}%` }} role="img" aria-label={`${s.label}: ${used.filter((v) => s.vendors[v.key]).map((v) => `${v.label} ${eur(s.vendors[v.key])}`).join(", ")}`}>
                {used.filter((v) => s.vendors[v.key]).map((v) => (
                  <div key={v.key} className="first:rounded-l last:rounded-r" style={{ width: `${(s.vendors[v.key] / s.total_eur) * 100}%`, background: colour(v.key) }} />
                ))}
              </div>
              <span className="text-right tabular-nums">{eur(s.total_eur)}</span>
            </div>
            {open === s.key && (
              <div className="pointer-events-none absolute left-40 top-5 z-10 w-56 rounded-md border bg-card p-2 text-xs shadow-md" data-testid="phase-tooltip">
                <div className="mb-1 font-medium">{s.label}</div>
                {used.filter((v) => s.vendors[v.key]).sort((a, b) => s.vendors[b.key] - s.vendors[a.key]).map((v) => (
                  <div key={v.key} className="flex items-center justify-between gap-3">
                    <span className="flex items-center gap-1.5 text-muted-foreground"><span className="inline-block size-2.5 rounded-sm" style={{ background: colour(v.key) }} />{v.label}</span>
                    <span className="font-semibold tabular-nums">{eur(s.vendors[v.key])} <span className="font-normal text-muted-foreground">{Math.round((s.vendors[v.key] / s.total_eur) * 100)}%</span></span>
                  </div>
                ))}
                {s.estimated_eur > 0 && <div className="mt-1 border-t pt-1 text-amber-600 dark:text-amber-400">~{eur(s.estimated_eur)} of it only estimated</div>}
              </div>
            )}
          </li>
        ))}
      </ul>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {used.map((v) => <li key={v.key} className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-sm" style={{ background: colour(v.key) }} />{v.label}</li>)}
      </ul>
    </div>
  );
}

/** The languages side by side: one small chart per language, a bar per phase, all on the same scale, so the shape of a language's spending can be compared at a glance. */
export function PhaseLanguages({ data }: { data: CostPhases }) {
  const languages = data.languages.filter((l) => data.steps.some((s) => s.languages[l]));
  const totals = Object.fromEntries(languages.map((l) => [l, data.steps.reduce((sum, s) => sum + (s.languages[l] ?? 0), 0)]));
  const max = Math.max(...data.steps.flatMap((s) => languages.map((l) => s.languages[l] ?? 0)), 0.0001);
  return (
    <div data-testid="phase-languages">
      <p className="mb-2 text-xs text-muted-foreground">Every panel uses the same scale (the longest bar is {eur(max, 0)}). Only money that belongs to one language is shown; shared work is in the other chart.</p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {[...languages].sort((a, b) => totals[b] - totals[a]).map((l) => (
          <div key={l} className="rounded-md border p-2" data-testid={`phase-language-${l}`}>
            <div className="mb-1 flex items-baseline justify-between text-xs"><span className="font-medium">{langLabel(l)}</span><span className="tabular-nums">{eur(totals[l])}</span></div>
            <ul className="space-y-0.5">
              {data.steps.filter((s) => !s.shared || s.languages[l]).map((s) => {
                const value = s.languages[l] ?? 0;
                return (
                  <li key={s.key} className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-1.5 text-[10px]" title={`${langLabel(l)} · ${s.label}: ${eur(value)}`}>
                    <span className="truncate text-muted-foreground">{s.label}</span>
                    <div className="h-2.5"><div className="h-2.5 rounded-r" style={{ width: `${(value / max) * 100}%`, minWidth: value ? 2 : 0, background: "var(--viz-1)" }} /></div>
                    <span className="text-right tabular-nums">{value ? eur(value) : "–"}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Both phase charts for all cases (caseId null) or one case. */
export function PhaseCharts({ caseId }: { caseId: number | null }) {
  const { data, error, refetch } = useApi(() => api.costsPhases(caseId), [caseId], { keepPrevious: true });
  if (error && !data) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return <Skeleton className="h-72" />;
  return (
    <div className="space-y-4">
      <Card className="viz-root">
        <CardHeader><CardTitle>Where the money went: phase by phase, split by vendor</CardTitle></CardHeader>
        <CardContent><PhaseVendors data={data} /></CardContent>
      </Card>
      <Card className="viz-root">
        <CardHeader><CardTitle>Languages compared, phase by phase</CardTitle></CardHeader>
        <CardContent><PhaseLanguages data={data} /></CardContent>
      </Card>
    </div>
  );
}
