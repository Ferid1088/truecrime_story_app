"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CostRow, CostSeries } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { eur } from "./shared";

/** One colour per entity (never per rank): the vendors and the five steps that have a slot of the palette; everything else is gray. */
const SLOT: Record<string, string> = {
  apimaster: "var(--viz-1)", heygen: "var(--viz-2)", elevenlabs: "var(--viz-3)", brave: "var(--viz-4)", apiko: "var(--viz-5)",
  visuals: "var(--viz-1)", text_polish: "var(--viz-2)", voice: "var(--viz-3)", avatar: "var(--viz-4)", language_text: "var(--viz-5)",
};
const colour = (key: string) => SLOT[key] ?? "var(--viz-other)";

const W = 720;
const H = 250;
const M = { l: 48, r: 18, t: 14, b: 26 };

function niceMax(value: number): { max: number; step: number } {
  if (value <= 0) return { max: 1, step: 0.25 };
  const raw = value / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? pow * 10;
  return { max: Math.ceil(value / step) * step, step };
}

const tick = (v: number) => (v >= 1000 ? `€${Math.round(v / 100) / 10}K` : v >= 10 ? `€${Math.round(v)}` : `€${v.toFixed(v % 1 === 0 ? 0 : 2)}`);

function labelOf(label: string, bucket: "day" | "hour"): string {
  return bucket === "hour" ? `${label.slice(11, 13)}:00` : label.slice(5);
}

/** Lines of the money over time. The crosshair snaps to a day or hour and one tooltip lists every line; "Table" shows the same figures as numbers. */
export function SpendChart({ caseId, title = "Spending over time" }: { caseId: number | null; title?: string }) {
  const [by, setBy] = useState<"vendor" | "step">("vendor");
  const [bucket, setBucket] = useState<"day" | "hour">("day");
  const [cumulative, setCumulative] = useState(false);
  const [table, setTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const { data, error, refetch } = useApi(() => api.costsSeries(by, bucket, caseId), [by, bucket, caseId], { keepPrevious: true });

  const lines = useMemo(() => {
    if (!data) return [];
    return data.series.map((s) => {
      let run = 0;
      return { ...s, shown: cumulative ? s.values.map((v) => (run += v)) : s.values };
    });
  }, [data, cumulative]);

  return (
    <Card className="viz-root" data-testid="spend-chart">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>{title}</CardTitle>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          {([["vendor", "By vendor"], ["step", "By step"]] as const).map(([k, l]) => <Button key={k} size="sm" variant={by === k ? "default" : "outline"} onClick={() => setBy(k)} data-testid={`spend-by-${k}`}>{l}</Button>)}
          <span className="mx-1 text-muted-foreground">|</span>
          {([["day", "30 days"], ["hour", "24 hours"]] as const).map(([k, l]) => <Button key={k} size="sm" variant={bucket === k ? "default" : "outline"} onClick={() => { setBucket(k); setHover(null); }} data-testid={`spend-range-${k}`}>{l}</Button>)}
          <span className="mx-1 text-muted-foreground">|</span>
          <Button size="sm" variant={cumulative ? "default" : "outline"} onClick={() => setCumulative(!cumulative)} data-testid="spend-cumulative">Running total</Button>
          <Button size="sm" variant={table ? "default" : "outline"} onClick={() => setTable(!table)} data-testid="spend-table">Table</Button>
        </div>
      </CardHeader>
      <CardContent>
        {error && !data && <ErrorState message={error} onRetry={refetch} />}
        {!data && !error && <Skeleton className="h-60" />}
        {data && (table ? <SeriesTable data={data} /> : <Lines data={data} lines={lines} hover={hover} setHover={setHover} cumulative={cumulative} />)}
      </CardContent>
    </Card>
  );
}

type Line = CostSeries["series"][number] & { shown: number[] };

function Lines({ data, lines, hover, setHover, cumulative }: { data: CostSeries; lines: Line[]; hover: number | null; setHover: (i: number | null) => void; cumulative: boolean }) {
  const n = data.labels.length;
  const top = Math.max(...lines.flatMap((l) => l.shown), 0);
  const { max, step } = niceMax(top);
  const x = (i: number) => M.l + (i / Math.max(n - 1, 1)) * (W - M.l - M.r);
  const y = (v: number) => M.t + (1 - v / max) * (H - M.t - M.b);
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const every = Math.ceil(n / 6);
  const at = hover ?? null;
  const total = (i: number) => lines.reduce((s, l) => s + l.shown[i], 0);
  return (
    <div className="relative" data-testid="spend-lines">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Money spent per ${data.bucket}, ${lines.map((l) => l.label).join(", ")}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} stroke="currentColor" strokeOpacity={0.12} strokeWidth={1} />
            <text x={M.l - 8} y={y(t) + 3} textAnchor="end" className="fill-muted-foreground" fontSize={10}>{tick(t)}</text>
          </g>
        ))}
        {data.labels.map((label, i) => (i % every === 0 || i === n - 1) && (
          <text key={label} x={x(i)} y={H - 8} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>{labelOf(label, data.bucket)}</text>
        ))}
        {at != null && <line x1={x(at)} x2={x(at)} y1={M.t} y2={H - M.b} stroke="currentColor" strokeOpacity={0.35} strokeWidth={1} />}
        {lines.map((l) => (
          <g key={l.key}>
            <polyline fill="none" stroke={colour(l.key)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" points={l.shown.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
            <circle cx={x(n - 1)} cy={y(l.shown[n - 1])} r={4} fill={colour(l.key)} stroke="var(--card)" strokeWidth={2} />
            {at != null && <circle cx={x(at)} cy={y(l.shown[at])} r={4} fill={colour(l.key)} stroke="var(--card)" strokeWidth={2} />}
          </g>
        ))}
        <rect x={M.l} y={0} width={W - M.l - M.r} height={H} fill="transparent" onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover(Math.min(Math.max(Math.round(((e.clientX - box.left) / box.width) * (n - 1)), 0), n - 1));
        }} onPointerLeave={() => setHover(null)} />
      </svg>
      {at != null && (
        <div className="pointer-events-none absolute top-2 z-10 w-56 rounded-md border bg-card p-2 text-xs shadow-md" style={{ left: `${(x(at) / W) * 100}%`, transform: x(at) > W * 0.6 ? "translateX(calc(-100% - 12px))" : "translateX(12px)" }} data-testid="spend-tooltip">
          <div className="mb-1 text-muted-foreground">{data.labels[at]}{data.bucket === "hour" ? ` (${data.timezone})` : ""}</div>
          {[...lines].sort((a, b) => b.shown[at] - a.shown[at]).map((l) => (
            <div key={l.key} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5 text-muted-foreground"><span className="inline-block h-0.5 w-3 rounded" style={{ background: colour(l.key) }} />{l.label}</span>
              <span className="font-semibold tabular-nums">{eur(l.shown[at])}</span>
            </div>
          ))}
          <div className="mt-1 flex justify-between border-t pt-1"><span className="text-muted-foreground">{cumulative ? "Running total" : "Together"}</span><span className="font-semibold tabular-nums">{eur(total(at))}</span></div>
        </div>
      )}
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="spend-legend">
        {lines.map((l) => (
          <li key={l.key} className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-4 rounded" style={{ background: colour(l.key) }} />{l.label}<span className="text-muted-foreground tabular-nums">{eur(l.values.reduce((s, v) => s + v, 0))}</span></li>
        ))}
      </ul>
    </div>
  );
}

function SeriesTable({ data }: { data: CostSeries }) {
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-xs tabular-nums" data-testid="spend-series-table">
        <thead><tr className="text-left text-muted-foreground"><th className="py-1">{data.bucket === "hour" ? `Hour (${data.timezone})` : "Day"}</th>{data.series.map((s) => <th key={s.key} className="text-right">{s.label}</th>)}<th className="text-right">Together</th></tr></thead>
        <tbody>
          {[...data.labels.keys()].reverse().map((i) => (
            <tr key={data.labels[i]} className="border-t"><td className="py-1">{data.labels[i]}</td>{data.series.map((s) => <td key={s.key} className="text-right">{eur(s.values[i])}</td>)}<td className="text-right font-medium">{eur(data.series.reduce((a, s) => a + s.values[i], 0))}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CERTAINTY: [string, string, string][] = [
  ["reported", "Reported by the provider", "var(--viz-3)"],
  ["computed", "Computed from usage and the price table", "var(--viz-1)"],
  ["estimated", "Only estimated", "var(--viz-4)"],
];

/** How sure the prices are: one whole (all the real money) split into the three kinds of price, a tall segmented bar with its legend. */
export function CertaintyBar({ rows }: { rows: CostRow[] }) {
  const parts = CERTAINTY.map(([key, label, fill]) => ({ key, label, fill, eur: rows.find((r) => r.key === key)?.eur ?? 0 })).filter((p) => p.eur > 0);
  const total = parts.reduce((s, p) => s + p.eur, 0);
  if (!total) return null;
  return (
    <Card className="viz-root" data-testid="certainty-bar">
      <CardHeader><CardTitle>How sure are the prices?</CardTitle></CardHeader>
      <CardContent>
        <div className="flex h-7 gap-0.5" role="img" aria-label={parts.map((p) => `${p.label}: ${eur(p.eur)}`).join("; ")}>
          {parts.map((p) => {
            const share = p.eur / total;
            return (
              <div key={p.key} className="flex items-center overflow-hidden rounded px-2 text-[11px] font-medium text-black/85" style={{ width: `${share * 100}%`, background: p.fill }} title={`${p.label}: ${eur(p.eur)} (${Math.round(share * 100)}%)`}>
                {share > 0.12 ? `${Math.round(share * 100)}%` : ""}
              </div>
            );
          })}
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {parts.map((p) => <li key={p.key} className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-sm" style={{ background: p.fill }} />{p.label}</li>)}
        </ul>
      </CardContent>
    </Card>
  );
}

/** Own spending and the share of the shared work per film minute, one stacked bar per language (the longer the bar, the dearer the film). */
export function MinuteBars({ rows }: { rows: { language: string; own_eur: number; with_shared_eur: number; film_minutes: number | null; eur_per_film_minute: number | null }[] }) {
  const usable = rows.filter((r) => r.film_minutes && r.eur_per_film_minute != null);
  const max = Math.max(...usable.map((r) => r.eur_per_film_minute as number), 0.0001);
  if (!usable.length) return null;
  return (
    <Card className="viz-root" data-testid="minute-bars">
      <CardHeader><CardTitle>Price of a finished film minute</CardTitle></CardHeader>
      <CardContent>
        <ul className="space-y-1.5">
          {[...usable].sort((a, b) => (b.eur_per_film_minute as number) - (a.eur_per_film_minute as number)).map((r) => {
            const minutes = r.film_minutes as number;
            const own = r.own_eur / minutes;
            const shared = (r.eur_per_film_minute as number) - own;
            return (
              <li key={r.language} className="grid grid-cols-[2.5rem_1fr_4.5rem] items-center gap-2 text-xs" title={`${r.language.toUpperCase()}: own ${eur(own)} + shared ${eur(shared)} per film minute`}>
                <span className="font-medium uppercase">{r.language}</span>
                <div className="flex h-4 gap-0.5">
                  <div className="rounded-l rounded-r-sm" style={{ width: `${(own / max) * 100}%`, background: "var(--viz-1)" }} />
                  <div className="rounded-r rounded-l-sm" style={{ width: `${(shared / max) * 100}%`, background: "var(--viz-other)" }} />
                </div>
                <span className="text-right tabular-nums">{eur(r.eur_per_film_minute)}</span>
              </li>
            );
          })}
        </ul>
        <ul className="mt-2 flex gap-4 text-xs">
          <li className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-sm" style={{ background: "var(--viz-1)" }} />Own spending</li>
          <li className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-sm" style={{ background: "var(--viz-other)" }} />Share of the shared work</li>
        </ul>
      </CardContent>
    </Card>
  );
}
