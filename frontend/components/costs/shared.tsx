"use client";

import { Fragment, useState } from "react";
import { formatDateTime } from "@/lib/format";
import type { CostCall, CostRow } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export function eur(value: number | null | undefined, digits = 2): string {
  if (value == null) return "–";
  return `€${value.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function Money({ value, estimated, digits = 2, className }: { value: number; estimated?: number; digits?: number; className?: string }) {
  return (
    <span className={cn("tabular-nums", className)}>
      {eur(value, digits)}
      {estimated ? <span className="ml-1 text-[10px] text-amber-600 dark:text-amber-400" title="This part is only estimated (the provider reported no price)">~{eur(estimated, digits)}</span> : null}
    </span>
  );
}

const ACCURACY: Record<string, { label: string; variant: "success" | "warning" | "outline" | "info" }> = {
  reported: { label: "reported", variant: "success" },
  computed: { label: "computed", variant: "info" },
  estimated: { label: "estimated", variant: "warning" },
  none: { label: "no price", variant: "outline" },
};

export function AccuracyBadge({ value }: { value: string | null }) {
  const found = ACCURACY[value ?? "none"] ?? ACCURACY.none;
  return <Badge variant={found.variant}>{found.label}</Badge>;
}

/** Horizontal bars: the money of each row against the biggest one; a click on a row calls onPick. */
export function Bars({ rows, onPick, testId }: { rows: CostRow[]; onPick?: (row: CostRow) => void; testId?: string }) {
  const max = Math.max(...rows.map((r) => r.eur), 0.0001);
  return (
    <ul className="space-y-1" data-testid={testId}>
      {rows.map((row) => (
        <li key={String(row.key)}>
          <button type="button" disabled={!onPick} onClick={() => onPick?.(row)} className="group block w-full text-left disabled:cursor-default">
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate group-hover:underline">{row.label}</span>
              <Money value={row.eur} estimated={row.estimated_eur} />
            </div>
            <div className="mt-0.5 h-1.5 rounded bg-muted">
              <div className="h-1.5 rounded bg-primary" style={{ width: `${Math.max((row.eur / max) * 100, 1)}%` }} />
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function Detail({ call }: { call: CostCall }) {
  const fields: [string, string][] = [
    ["Call", `#${call.id}`], ["Time", formatDateTime(call.created_at)], ["Step", call.step ?? "–"], ["Language", call.language ?? "unknown"], ["Vendor", call.vendor ?? "–"],
    ["Host", call.host], ["Model", call.model ?? "–"], ["Stage", call.stage ?? "–"], ["Object", call.subject ?? "–"], ["Units", call.units?.toString() ?? "–"],
    ["Tokens in / out", `${call.tokens_in ?? "–"} / ${call.tokens_out ?? "–"}`], ["Tokens cached", call.tokens_cached?.toString() ?? "–"], ["Price", eur(call.cost_eur, 6)],
    ["Price source", call.cost_source ?? call.accuracy ?? "–"], ["Took", `${call.duration_ms} ms`], ["Result", call.ok ? "ok" : `failed: ${call.error ?? ""}`],
    ["Request id", call.request_id ?? "–"], ["Found by", call.attribution === "live" ? "written with the call" : `rule (${call.attribution ?? "–"})`],
    ["Counted", call.duplicate_of ? `no, repeats call #${call.duplicate_of}` : "yes"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-1 p-3 text-xs md:grid-cols-3" data-testid="call-detail">
      {fields.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="break-all font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The single paid calls; a click opens everything the ledger knows about one call (the atom). */
export function CallTable({ calls }: { calls: CostCall[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Table data-testid="call-table">
      <THead>
        <TR>
          <TH>Time</TH>
          <TH>Step</TH>
          <TH>Language</TH>
          <TH>Vendor</TH>
          <TH>Model</TH>
          <TH className="text-right">Price</TH>
          <TH>Price is</TH>
        </TR>
      </THead>
      <TBody>
        {calls.map((call) => (
          <Fragment key={call.id}>
            <TR className={cn("cursor-pointer hover:bg-muted/50", call.duplicate_of && "opacity-50")} onClick={() => setOpen(open === call.id ? null : call.id)} data-testid={`call-${call.id}`}>
              <TD className="whitespace-nowrap text-xs">{formatDateTime(call.created_at)}</TD>
              <TD className="text-xs">{call.step ?? "–"}</TD>
              <TD className="text-xs">{call.language ?? "–"}</TD>
              <TD className="text-xs">{call.vendor ?? "–"}</TD>
              <TD className="max-w-[14rem] truncate text-xs">{call.model ?? "–"}</TD>
              <TD className="text-right text-xs tabular-nums">{eur(call.cost_eur, 4)}</TD>
              <TD><AccuracyBadge value={call.accuracy} /></TD>
            </TR>
            {open === call.id && (
              <TR>
                <TD colSpan={7} className="bg-muted/30 p-0"><Detail call={call} /></TD>
              </TR>
            )}
          </Fragment>
        ))}
      </TBody>
    </Table>
  );
}
