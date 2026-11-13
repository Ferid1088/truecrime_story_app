"use client";

import { useState } from "react";
import { ChevronRight, X } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CostFilters } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { Bars, CallTable, Money } from "./shared";

const CHAIN = ["step", "language", "vendor", "model", "subject"] as const;
const NAMES: Record<string, string> = { step: "Step", language: "Language", vendor: "Vendor", model: "Model", subject: "Object", accuracy: "Price is", day: "Day" };

export type Chip = { dimension: string; value: string; label: string };

/** Click your way down: every row you click becomes a filter, the next dimension opens, and at the end (or at any time) the single calls are shown. */
export function Drill({ caseId, chips, onChips }: { caseId: number; chips: Chip[]; onChips: (next: Chip[]) => void }) {
  const [calls, setCalls] = useState(false);
  const [offset, setOffset] = useState(0);
  const filters: CostFilters = Object.fromEntries(chips.map((c) => [c.dimension, c.value]));
  const next = CHAIN.find((d) => !(d in filters));
  const showCalls = calls || !next;
  const key = JSON.stringify(filters);
  const group = useApi(() => (showCalls ? Promise.resolve(null) : api.costsGroup(caseId, next as string, filters)), [caseId, key, showCalls]);
  const list = useApi(() => (showCalls ? api.costsCalls(caseId, filters, offset) : Promise.resolve(null)), [caseId, key, showCalls, offset]);
  const total = (group.data?.totals ?? null);

  const pick = (dimension: string, value: string, label: string) => {
    setOffset(0);
    setCalls(false);
    onChips([...chips, { dimension, value, label }]);
  };

  return (
    <Card data-testid="cost-drill">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle className="mr-2">Drill down</CardTitle>
        <button type="button" className="text-xs text-primary hover:underline" onClick={() => { setCalls(false); onChips([]); }}>All spending</button>
        {chips.map((chip, i) => (
          <span key={chip.dimension} className="inline-flex items-center gap-1 text-xs">
            <ChevronRight className="size-3 text-muted-foreground" aria-hidden />
            <button type="button" className="rounded border px-1.5 py-0.5 hover:bg-muted" onClick={() => { setCalls(false); onChips(chips.slice(0, i + 1)); }}>
              {NAMES[chip.dimension]}: {chip.label}
            </button>
            <button type="button" aria-label={`Remove ${chip.label}`} onClick={() => { setCalls(false); onChips(chips.filter((c) => c !== chip)); }}><X className="size-3" /></button>
          </span>
        ))}
        {chips.length > 0 && !showCalls && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => setCalls(true)} data-testid="show-calls">Show the single calls</Button>
        )}
        {chips.length > 0 && showCalls && next && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => setCalls(false)}>Back to groups</Button>
        )}
      </CardHeader>
      <CardContent>
        {(group.error || list.error) && <ErrorState message={(group.error ?? list.error) as string} onRetry={() => { group.refetch(); list.refetch(); }} />}
        {!showCalls && (group.loading || !group.data) && !group.error && <Skeleton className="h-32" />}
        {!showCalls && group.data && (
          <>
            <p className="mb-2 text-xs text-muted-foreground">
              Split by <strong>{NAMES[next as string]}</strong> · together <Money value={total?.eur ?? 0} estimated={total?.estimated_eur} /> in {total?.calls ?? 0} priced calls. Click a row to go on.
            </p>
            <Bars rows={group.data.rows} onPick={(row) => pick(next as string, String(row.key), row.label)} testId="drill-rows" />
          </>
        )}
        {showCalls && list.data && (
          <>
            <p className="mb-2 text-xs text-muted-foreground">{list.data.total} calls, the dearest first. Click a call for everything the ledger knows about it. Faded rows only repeat another call and are not counted.</p>
            <CallTable calls={list.data.calls} />
            <div className="mt-2 flex items-center gap-2 text-xs">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - 50, 0))}>Previous</Button>
              <Button size="sm" variant="outline" disabled={offset + 50 >= list.data.total} onClick={() => setOffset(offset + 50)}>Next</Button>
              <span className="text-muted-foreground">{offset + 1}–{Math.min(offset + 50, list.data.total)}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
