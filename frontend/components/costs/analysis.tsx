"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Bars, CallTable, DailyBars, eur } from "./shared";

function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

/** What is worth looking at: pace and days to the cap, the price of a film minute, waste, the dearest calls and models, the prompt cache and sudden jumps. */
export function Analysis({ caseId }: { caseId: number }) {
  const { data, error, refetch } = useApi(() => api.costsAnalysis(caseId), [caseId]);
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return <Skeleton className="h-48" />;
  const { pace, waste, cache, shorts } = data;
  return (
    <div className="space-y-4" data-testid="cost-analysis">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Fact label="Last 24 hours" value={eur(pace.eur_last_24h)} />
        <Fact label="Per day (last 3 days)" value={eur(pace.eur_per_day_recent)} hint={pace.days_until_cap != null ? `${pace.days_until_cap} days until the cap at this pace` : undefined} />
        <Fact label="Failed calls" value={eur(waste.failed_calls_eur)} hint="paid but without a result" />
        <Fact label="Repair rounds" value={eur(waste.repair_calls_eur)} hint="calls of a stage called repair" />
        <Fact label="Prompt cache" value={cache.share == null ? "–" : `${Math.round(cache.share * 100)}%`} hint={`${cache.tokens_cached.toLocaleString("en-GB")} of ${cache.tokens_in.toLocaleString("en-GB")} input tokens`} />
        <Fact label="Short clips with video" value={String(shorts.clips_with_video)} hint={`writing and covers ${eur(shorts.writing_and_covers_eur)} (voice and avatar are in their own steps)`} />
      </div>
      {data.spikes.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Steps that cost much more than usual in the last 24 hours</CardTitle></CardHeader>
          <CardContent className="text-xs">
            <ul className="space-y-1" data-testid="cost-spikes">
              {data.spikes.map((s) => <li key={s.step}><strong>{s.label}</strong>: {eur(s.last_24h_eur)} against about {eur(s.usual_per_day_eur)} a day</li>)}
            </ul>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader><CardTitle>Price of a finished film minute, per language</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <THead><TR><TH>Language</TH><TH className="text-right">Own spending</TH><TH className="text-right">With its share of the shared work</TH><TH className="text-right">Film minutes</TH><TH className="text-right">Per film minute</TH></TR></THead>
            <TBody>
              {data.per_language.map((l) => (
                <TR key={l.language}>
                  <TD>{langLabel(l.language)}</TD>
                  <TD className="text-right tabular-nums">{eur(l.own_eur)}</TD>
                  <TD className="text-right tabular-nums">{eur(l.with_shared_eur)}</TD>
                  <TD className="text-right tabular-nums">{l.film_minutes ?? "–"}</TD>
                  <TD className="text-right tabular-nums">{eur(l.eur_per_film_minute)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader><CardTitle>Per day</CardTitle></CardHeader><CardContent><DailyBars rows={data.daily} /></CardContent></Card>
        <Card><CardHeader><CardTitle>Models</CardTitle></CardHeader><CardContent><Bars rows={data.models} /></CardContent></Card>
      </div>
      <Card><CardHeader><CardTitle>The 20 dearest single calls</CardTitle></CardHeader><CardContent><CallTable calls={data.top_calls} /></CardContent></Card>
    </div>
  );
}
