"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CostReconciliation } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { LiveFeed } from "./live";
import { CertaintyBar, SpendChart } from "./charts";
import { PhaseCharts } from "./phases";
import { Bars, Money, eur } from "./shared";

function Invoices({ rows, vendors, onChanged }: { rows: CostReconciliation[]; vendors: { key: string; label: string }[]; onChanged: () => void }) {
  const [vendor, setVendor] = useState(vendors[0]?.key ?? "");
  const [period, setPeriod] = useState(new Date().toISOString().slice(0, 7));
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    try {
      await api.saveInvoice({ vendor, period, amount_eur: Number(amount.replace(",", ".")), note: note || null });
      setAmount("");
      setNote("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    }
  };
  return (
    <Card data-testid="cost-invoices">
      <CardHeader><CardTitle>The real bills against the ledger</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">Type in what a vendor really charged for a month (from the invoice or the vendor&apos;s usage page). The ledger is the app&apos;s own record; the difference shows how far it is off.</p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">Vendor<select className="mt-1 block h-9 rounded-md border bg-background px-2 text-sm" value={vendor} onChange={(e) => setVendor(e.target.value)} data-testid="invoice-vendor">{vendors.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}</select></label>
          <label className="text-xs">Month<Input className="mt-1 w-28" value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-11" data-testid="invoice-period" /></label>
          <label className="text-xs">Amount (EUR)<Input className="mt-1 w-28" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" data-testid="invoice-amount" /></label>
          <label className="text-xs">Note<Input className="mt-1 w-56" value={note} onChange={(e) => setNote(e.target.value)} /></label>
          <Button size="sm" onClick={save} disabled={!amount} data-testid="invoice-save">Save bill</Button>
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        <Table>
          <THead><TR><TH>Month</TH><TH>Vendor</TH><TH className="text-right">Ledger</TH><TH className="text-right">Real bill</TH><TH className="text-right">Difference</TH><TH /></TR></THead>
          <TBody>
            {rows.map((r) => (
              <TR key={`${r.vendor}-${r.period}`}>
                <TD className="text-xs">{r.period}</TD>
                <TD className="text-xs">{r.label}</TD>
                <TD className="text-right text-xs"><Money value={r.ledger_eur} estimated={r.ledger_estimated_eur} /></TD>
                <TD className="text-right text-xs tabular-nums">{eur(r.invoice_eur)}</TD>
                <TD className={`text-right text-xs tabular-nums ${r.difference_eur != null && Math.abs(r.difference_eur) > 1 ? "text-amber-600" : ""}`}>{r.difference_eur == null ? "no bill yet" : eur(r.difference_eur)}</TD>
                <TD className="text-right">{r.invoice_id != null && <Button size="sm" variant="outline" onClick={async () => { await api.deleteInvoice(r.invoice_id as number); onChanged(); }}>Remove</Button>}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

/** All cases at a glance: the money really spent, who it went to, which steps and languages, which case; click a case to open its own analysis. */
export function CostsOverview() {
  const { data, error, refetch } = useApi(() => api.costsOverview(), [], { keepPrevious: true });
  if (error && !data) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return <Skeleton className="h-64" />;
  const t = data.totals;
  return (
    <div className="space-y-4" data-testid="costs-overview">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="cost-kpis">
        {[
          ["Real money spent", <Money key="a" value={t.real_eur} />, "what the calls cost, each paid call once"],
          ["Of that only estimated", <Money key="b" value={t.estimated_eur} />, "the provider reported no price"],
          ["Left out as repeats", <Money key="c" value={t.repeats_eur} />, `ledger gross ${eur(t.gross_eur)}`],
          ["Priced calls", <span key="d">{t.priced_calls.toLocaleString("en-GB")}</span>, undefined],
        ].map(([label, value, hint]) => (
          <div key={label as string} className="rounded-md border p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label as string}</div>
            <div className="text-xl font-semibold tabular-nums">{value as React.ReactNode}</div>
            {hint ? <div className="text-[11px] text-muted-foreground">{hint as string}</div> : null}
          </div>
        ))}
      </div>
      <PhaseCharts caseId={null} />
      <div className="grid items-start gap-4 lg:grid-cols-[2fr_1fr]">
        <SpendChart caseId={null} />
        <div className="space-y-4">
          <CertaintyBar rows={data.accuracy} />
          <Card><CardHeader><CardTitle>Per vendor</CardTitle></CardHeader><CardContent><Bars rows={data.by_vendor} /></CardContent></Card>
        </div>
      </div>
      <LiveFeed key="all" caseId={null} />
      <Card>
        <CardHeader><CardTitle>Cases</CardTitle></CardHeader>
        <CardContent>
          <Table data-testid="cost-cases">
            <THead><TR><TH>Case</TH><TH className="text-right">Real money</TH><TH className="text-right">Estimated part</TH><TH className="text-right">Priced calls</TH></TR></THead>
            <TBody>
              {data.cases.map((c) => (
                <TR key={String(c.case_id)}>
                  <TD className="text-xs">{c.exists && c.case_id != null ? <Link href={`/costs/${c.case_id}`} className="font-medium text-primary hover:underline" data-testid={`cost-case-${c.case_id}`}>{c.title}</Link> : c.title}</TD>
                  <TD className="text-right text-xs"><Money value={c.eur} /></TD>
                  <TD className="text-right text-xs"><Money value={c.estimated_eur} /></TD>
                  <TD className="text-right text-xs tabular-nums">{c.calls.toLocaleString("en-GB")}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader><CardTitle>Per step</CardTitle></CardHeader><CardContent><Bars rows={data.by_step} /></CardContent></Card>
        <Card><CardHeader><CardTitle>Per language</CardTitle></CardHeader><CardContent><Bars rows={data.by_language} /></CardContent></Card>
      </div>
      <Invoices rows={data.reconciliation} vendors={data.vendors} onChanged={refetch} />
    </div>
  );
}
