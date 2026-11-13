"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CostCell, CostMatrix, CostVendorMatrix } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Analysis } from "./analysis";
import { Drill, type Chip } from "./drill";
import { LiveFeed } from "./live";
import { CertaintyBar, SpendChart } from "./charts";
import { PhaseCharts } from "./phases";
import { Bars, Money, eur, useLiveTick } from "./shared";

type Pick = (cell: { step?: [string, string]; language?: [string, string]; vendor?: [string, string] }) => void;

/** Heat: the deeper the blue, the bigger the share of the dearest cell (square root, so small cells still show). */
function heat(value: number, max: number): string | undefined {
  if (!max || value <= 0) return undefined;
  return `color-mix(in srgb, var(--viz-1) ${Math.round(Math.sqrt(value / max) * 42)}%, transparent)`;
}

function Cell({ cell, allocated, onClick, bold, max }: { cell?: CostCell; allocated?: boolean; onClick?: () => void; bold?: boolean; max?: number }) {
  const value = cell?.eur ?? 0;
  const share = allocated ? cell?.allocated_eur ?? 0 : 0;
  if (!value && !share) return <TD className="text-right text-xs text-muted-foreground">–</TD>;
  return (
    <TD className="text-right text-xs" style={{ background: max ? heat(value, max) : undefined }}>
      <button type="button" onClick={onClick} className={`tabular-nums hover:underline ${bold ? "font-semibold" : ""}`}>
        <Money value={value} estimated={cell?.estimated_eur} />
      </button>
      {share > 0 && <div className="text-[10px] text-muted-foreground" title="An even share of the shared work (not a fact)">+ {eur(share)} share</div>}
    </TD>
  );
}

function MatrixTable({ matrix, allocated, pick }: { matrix: CostMatrix; allocated: boolean; pick: Pick }) {
  const max = Math.max(...matrix.rows.flatMap((r) => matrix.columns.map((c) => r.cells[c]?.eur ?? 0)), 0);
  return (
    <div className="overflow-x-auto">
      <Table data-testid="cost-matrix">
        <THead>
          <TR>
            <TH>Step</TH>
            {matrix.columns.map((c) => <TH key={c} className="text-right">{matrix.labels[c]}</TH>)}
            <TH className="text-right">Sum</TH>
          </TR>
        </THead>
        <TBody>
          {matrix.rows.map((row) => (
            <TR key={row.step} data-testid={`matrix-row-${row.step}`}>
              <TD className="whitespace-nowrap text-xs font-medium">
                <button type="button" className="hover:underline" onClick={() => pick({ step: [row.step, row.label] })}>{row.label}</button>
              </TD>
              {matrix.columns.map((c) => (
                <Cell key={c} cell={row.cells[c]} allocated={allocated} max={max} onClick={() => pick({ step: [row.step, row.label], language: [c, matrix.labels[c]] })} />
              ))}
              <TD className="text-right text-xs font-semibold"><Money value={row.total_eur} estimated={row.total_estimated_eur} /></TD>
            </TR>
          ))}
          <TR className="border-t-2 font-semibold" data-testid="matrix-totals">
            <TD className="text-xs">Sum</TD>
            {matrix.columns.map((c) => (
              <Cell key={c} bold allocated={allocated} cell={{ eur: matrix.column_totals[c].eur, estimated_eur: matrix.column_totals[c].estimated_eur, calls: 0, allocated_eur: matrix.column_totals[c].allocated_eur }}
                onClick={() => pick({ language: [c, matrix.labels[c]] })} />
            ))}
            <TD className="text-right text-xs"><Money value={matrix.total_eur} estimated={matrix.total_estimated_eur} /></TD>
          </TR>
        </TBody>
      </Table>
    </div>
  );
}

function VendorTable({ matrix, pick }: { matrix: CostVendorMatrix; pick: Pick }) {
  const max = Math.max(...matrix.rows.flatMap((r) => matrix.columns.map((c) => r.cells[c]?.eur ?? 0)), 0);
  return (
    <div className="overflow-x-auto">
      <Table data-testid="cost-vendors">
        <THead>
          <TR>
            <TH>Vendor</TH>
            {matrix.columns.map((c) => <TH key={c} className="text-right">{matrix.labels[c]}</TH>)}
            <TH className="text-right">Sum</TH>
          </TR>
        </THead>
        <TBody>
          {matrix.rows.map((row) => (
            <TR key={row.vendor}>
              <TD className="whitespace-nowrap text-xs font-medium"><button type="button" className="hover:underline" onClick={() => pick({ vendor: [row.vendor, row.label] })}>{row.label}</button></TD>
              {matrix.columns.map((c) => <Cell key={c} cell={row.cells[c]} max={max} onClick={() => pick({ vendor: [row.vendor, row.label], language: [c, matrix.labels[c]] })} />)}
              <TD className="text-right text-xs font-semibold"><Money value={row.total_eur} estimated={row.total_estimated_eur} /></TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function Kpi({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{children}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

const VIEWS = ["Spending", "Live", "Analysis"] as const;

/** One case's costs: the figures at a glance, then the matrix of steps and languages, the vendors, and a drill-down from any figure to the single paid call. */
export function CaseCosts({ caseId, standalone = false }: { caseId: number; standalone?: boolean }) {
  const [allocation, setAllocation] = useState<"none" | "even">("none");
  const [view, setView] = useState<(typeof VIEWS)[number]>("Spending");
  const [chips, setChips] = useState<Chip[]>([]);
  const tick = useLiveTick();
  const { data, error, refetch } = useApi(() => api.costsCase(caseId, allocation), [caseId, allocation, tick], { keepPrevious: true });

  const pick: Pick = (cell) => {
    const next: Chip[] = [];
    for (const dimension of ["step", "language", "vendor"] as const) {
      const found = cell[dimension];
      if (found) next.push({ dimension, value: found[0], label: found[1] });
    }
    setChips(next);
    document.getElementById("cost-drill-anchor")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (error && !data) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return <Skeleton className="h-64" />;
  const { totals, budget } = data;
  return (
    <div className="space-y-4" data-testid="case-costs">
      {standalone && <div className="text-sm"><Link href="/costs" className="text-primary hover:underline">← All cases</Link> <span className="ml-2 font-medium">{data.title}</span></div>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="cost-kpis">
        <Kpi label="Real money spent" hint="what the calls cost, each paid call once"><Money value={totals.real_eur} /></Kpi>
        <Kpi label="Of that only estimated" hint="the provider reported no price (HeyGen, music, search)"><Money value={totals.estimated_eur} /></Kpi>
        <Kpi label="Budget counter" hint={budget ? `cap ${eur(budget.cap_eur, 0)} · since the reset (baseline ${eur(budget.baseline_eur)})` : undefined}>{budget ? eur(budget.counter_eur) : "–"}</Kpi>
        <Kpi label="Left out as repeats" hint={`ledger gross ${eur(totals.gross_eur)}: ${eur(totals.repeats_eur)} was written twice`}><Money value={totals.repeats_eur} /></Kpi>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {VIEWS.map((v) => <Button key={v} size="sm" variant={view === v ? "default" : "outline"} onClick={() => setView(v)} data-testid={`costs-view-${v}`}>{v}</Button>)}
        {totals.unlabelled_rows > 0 && <Badge variant="warning">{totals.unlabelled_rows} rows not labelled yet</Badge>}
      </div>
      {view === "Live" && <LiveFeed key={caseId} caseId={caseId} />}
      {view === "Analysis" && <Analysis caseId={caseId} />}
      {view === "Spending" && (
        <>
          <PhaseCharts key={`phases-${caseId}`} caseId={caseId} />
          <Card className="viz-root">
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle>Every step in every language</CardTitle>
              <div className="flex items-center gap-1 text-xs">
                <span className="text-muted-foreground">Shared work:</span>
                <Button size="sm" variant={allocation === "none" ? "default" : "outline"} onClick={() => setAllocation("none")} data-testid="alloc-none">Own column</Button>
                <Button size="sm" variant={allocation === "even" ? "default" : "outline"} onClick={() => setAllocation("even")} data-testid="alloc-even">Shared evenly over the languages</Button>
              </div>
            </CardHeader>
            <CardContent>
              <MatrixTable matrix={data.matrix} allocated={allocation === "even"} pick={pick} />
              <p className="mt-2 text-[11px] text-muted-foreground">
                “Shared” is work for all languages (research, story, pictures); “Language unknown” is spending whose language the ledger does not know. The evenly spread share is a calculation, not a booked cost. Click any figure to look inside it.
              </p>
            </CardContent>
          </Card>
          <div className="grid items-start gap-4 lg:grid-cols-[2fr_1fr]">
            <SpendChart key={`spend-${caseId}`} caseId={caseId} />
            <div className="space-y-4">
              <CertaintyBar rows={data.accuracy} />
              <Card><CardHeader><CardTitle>Per vendor</CardTitle></CardHeader><CardContent><Bars rows={data.by_vendor} onPick={(r) => pick({ vendor: [String(r.key), r.label] })} /></CardContent></Card>
            </div>
          </div>
          <Card className="viz-root"><CardHeader><CardTitle>Each vendor in each language</CardTitle></CardHeader><CardContent><VendorTable matrix={data.vendors} pick={pick} /></CardContent></Card>
          <div id="cost-drill-anchor"><Drill caseId={caseId} chips={chips} onChips={setChips} /></div>
        </>
      )}
    </div>
  );
}
