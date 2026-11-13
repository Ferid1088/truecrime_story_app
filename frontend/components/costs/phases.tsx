"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
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

/** Where the money went, phase by phase in the order of the pipeline: one bar per phase, split by vendor (APIMaster, ElevenLabs, HeyGen…). A click on a phase opens the parts
 *  it is made of (the agents or stages that paid: reading, punctuation, direct, verify…), each split by vendor, with the money per language of that part. */
export function PhaseVendors({ data }: { data: CostPhases }) {
  const [hover, setHover] = useState<string | null>(null);
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const max = Math.max(...data.steps.map((s) => s.total_eur), 0.0001);
  const used = data.vendors.filter((v) => data.steps.some((s) => s.vendors[v.key]));
  const toggle = (key: string) => setOpened((old) => {
    const next = new Set(old);
    if (!next.delete(key)) next.add(key);
    return next;
  });
  return (
    <div data-testid="phase-vendors">
      <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>Click a phase to see what it is made of.</span>
        <button type="button" className="text-primary hover:underline" onClick={() => setOpened(opened.size ? new Set() : new Set(data.steps.map((s) => s.key)))} data-testid="phase-expand-all">{opened.size ? "Close all" : "Open all"}</button>
      </div>
      <ul className="space-y-1.5">
        {data.steps.map((s) => (
          <li key={s.key} data-testid={`phase-${s.key}`}>
            <div className="relative" onPointerEnter={() => setHover(s.key)} onPointerLeave={() => setHover(null)}>
              <button type="button" className="grid w-full grid-cols-[10rem_1fr_5rem] items-center gap-2 text-left text-xs" onClick={() => toggle(s.key)} aria-expanded={opened.has(s.key)} data-testid={`phase-toggle-${s.key}`}>
                <span className="flex items-center gap-1 truncate"><ChevronRight className={`size-3 shrink-0 transition-transform ${opened.has(s.key) ? "rotate-90" : ""}`} aria-hidden />{s.label}</span>
                <div className="flex h-4 gap-0.5" style={{ width: `${Math.max((s.total_eur / max) * 100, 1)}%` }} role="img" aria-label={`${s.label}: ${used.filter((v) => s.vendors[v.key]).map((v) => `${v.label} ${eur(s.vendors[v.key])}`).join(", ")}`}>
                  {used.filter((v) => s.vendors[v.key]).map((v) => (
                    <div key={v.key} className="first:rounded-l last:rounded-r" style={{ width: `${(s.vendors[v.key] / s.total_eur) * 100}%`, background: colour(v.key) }} />
                  ))}
                </div>
                <span className="text-right tabular-nums">{eur(s.total_eur)}</span>
              </button>
              {hover === s.key && !opened.has(s.key) && (
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
            </div>
            {opened.has(s.key) && (
              <ul className="mb-2 ml-4 mt-1 space-y-1.5 border-l pl-3" data-testid={`phase-parts-${s.key}`}>
                {s.parts.map((p) => (
                  <li key={p.key} data-testid={`part-${s.key}-${p.key}`}>
                    <div className="grid grid-cols-[9rem_1fr_5rem] items-center gap-2 text-xs">
                      <span className="truncate">{p.label}</span>
                      <div className="flex h-3 gap-0.5" style={{ width: `${Math.max((p.eur / max) * 100, 0.6)}%` }} role="img" aria-label={`${p.label}: ${used.filter((v) => p.vendors[v.key]).map((v) => `${v.label} ${eur(p.vendors[v.key])}`).join(", ")}`}>
                        {used.filter((v) => p.vendors[v.key]).map((v) => <div key={v.key} className="first:rounded-l last:rounded-r" style={{ width: `${(p.vendors[v.key] / p.eur) * 100}%`, background: colour(v.key) }} />)}
                      </div>
                      <span className="text-right tabular-nums">{eur(p.eur)}</span>
                    </div>
                    <div className="ml-[9.5rem] mt-0.5 flex flex-wrap gap-x-3 text-[10px] text-muted-foreground" data-testid={`part-languages-${s.key}-${p.key}`}>
                      {Object.entries(p.languages).sort((a, b) => b[1] - a[1]).map(([l, v]) => (
                        <span key={l}>{l === "_shared" ? "shared" : l === "_unknown" ? "language unknown" : l.toUpperCase()} <span className="tabular-nums text-foreground">{eur(v)}</span></span>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
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

const sum = (parts: Record<string, number> | undefined) => Object.values(parts ?? {}).reduce((a, v) => a + v, 0);

/** The languages side by side: one small chart per language, a bar per phase split by vendor like the chart above, all on the same scale; the vendors' totals of each
 *  language stand under its name, and the exact figures of a phase appear when the pointer is on it. */
export function PhaseLanguages({ data }: { data: CostPhases }) {
  const [hover, setHover] = useState<string | null>(null);
  const languages = data.languages.filter((l) => data.steps.some((s) => sum(s.languages[l]) > 0));
  const totals = Object.fromEntries(languages.map((l) => [l, data.steps.reduce((acc, s) => acc + sum(s.languages[l]), 0)]));
  const max = Math.max(...data.steps.flatMap((s) => languages.map((l) => sum(s.languages[l]))), 0.0001);
  const used = data.vendors.filter((v) => data.steps.some((s) => languages.some((l) => s.languages[l]?.[v.key])));
  return (
    <div data-testid="phase-languages">
      <p className="mb-2 text-xs text-muted-foreground">Every panel uses the same scale (the longest bar is {eur(max, 0)}). A bar is split by vendor, like the chart above; point at a bar for the exact figures. Only money that belongs to one language is shown; shared work is in the other chart.</p>
      <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {used.map((v) => <li key={v.key} className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-sm" style={{ background: colour(v.key) }} />{v.label}</li>)}
      </ul>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {[...languages].sort((a, b) => totals[b] - totals[a]).map((l) => {
          const perVendor = used.map((v) => ({ ...v, eur: data.steps.reduce((acc, s) => acc + (s.languages[l]?.[v.key] ?? 0), 0) })).filter((v) => v.eur > 0);
          return (
            <div key={l} className="rounded-md border p-2" data-testid={`phase-language-${l}`}>
              <div className="flex items-baseline justify-between text-xs"><span className="font-medium">{langLabel(l)}</span><span className="tabular-nums">{eur(totals[l])}</span></div>
              <div className="mb-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground" data-testid={`phase-language-vendors-${l}`}>
                {perVendor.map((v) => <span key={v.key} className="flex items-center gap-1"><span className="inline-block size-2 rounded-sm" style={{ background: colour(v.key) }} />{v.label} <span className="tabular-nums text-foreground">{eur(v.eur)}</span></span>)}
              </div>
              <ul className="space-y-0.5">
                {data.steps.filter((s) => !s.shared || s.languages[l]).map((s) => {
                  const parts = s.languages[l] ?? {};
                  const total = sum(parts);
                  const id = `${l}-${s.key}`;
                  return (
                    <li key={s.key} className="relative grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-1.5 text-[10px]" onPointerEnter={() => setHover(id)} onPointerLeave={() => setHover(null)} data-testid={`phase-language-${l}-${s.key}`}>
                      <span className="truncate text-muted-foreground">{s.label}</span>
                      <div className="flex h-2.5 gap-px" style={{ width: `${(total / max) * 100}%`, minWidth: total ? 2 : 0 }} role="img" aria-label={`${langLabel(l)} · ${s.label}: ${used.filter((v) => parts[v.key]).map((v) => `${v.label} ${eur(parts[v.key])}`).join(", ")}`}>
                        {used.filter((v) => parts[v.key]).map((v) => <div key={v.key} className="first:rounded-l last:rounded-r" style={{ width: `${(parts[v.key] / total) * 100}%`, background: colour(v.key) }} />)}
                      </div>
                      <span className="text-right tabular-nums">{total ? eur(total) : "–"}</span>
                      {hover === id && total > 0 && (
                        <div className="pointer-events-none absolute left-28 top-3 z-10 w-52 rounded-md border bg-card p-2 text-xs shadow-md" data-testid="phase-language-tooltip">
                          <div className="mb-1 font-medium">{langLabel(l)} · {s.label}</div>
                          {used.filter((v) => parts[v.key]).sort((a, b) => parts[b.key] - parts[a.key]).map((v) => (
                            <div key={v.key} className="flex items-center justify-between gap-3">
                              <span className="flex items-center gap-1.5 text-muted-foreground"><span className="inline-block size-2.5 rounded-sm" style={{ background: colour(v.key) }} />{v.label}</span>
                              <span className="font-semibold tabular-nums">{eur(parts[v.key])} <span className="font-normal text-muted-foreground">{Math.round((parts[v.key] / total) * 100)}%</span></span>
                            </div>
                          ))}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
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
