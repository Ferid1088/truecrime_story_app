"use client";

import { useState } from "react";
import { opsApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { PolicyWarningNotice } from "@/components/policy-warning";

const WINDOWS = [
  { hours: 1, label: "1 hour" },
  { hours: 24, label: "24 hours" },
  { hours: 168, label: "7 days" },
];

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="py-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
}

export default function OpsPage() {
  const [hours, setHours] = useState(24);
  const summary = useApi(() => opsApi.summary(hours), [hours], { keepPrevious: true });
  const errors = useApi(() => opsApi.calls({ onlyErrors: true, limit: 20 }), [hours], { keepPrevious: true });
  const search = useApi(() => opsApi.searchStatus(), []);

  return (
    <div>
      <PageHeader
        title="Operations"
        description="Calls to outside services, their cost, and the state of the job queue."
        actions={
          <div role="group" aria-label="Time window" className="flex gap-1">
            {WINDOWS.map((w) => (
              <button
                key={w.hours}
                onClick={() => setHours(w.hours)}
                aria-pressed={hours === w.hours}
                className={`cursor-pointer rounded-md border px-2.5 py-1 text-xs ${
                  hours === w.hours ? "border-primary bg-muted font-medium" : "border-border text-muted-foreground"
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
        }
      />

      {summary.loading && !summary.data && <Skeleton className="h-40" />}
      {summary.error && <ErrorState message={summary.error} onRetry={summary.refetch} />}

      {summary.data && (
        <div className="space-y-4">
          <PolicyWarningNotice warning={search.data?.warning} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Calls" value={String(summary.data.total_calls)} />
            <Stat label="Failed" value={String(summary.data.total_errors)} />
            <Stat label="Cost (reported)" value={`$${summary.data.total_cost_usd.toFixed(2)}`} />
            <Stat
              label="Jobs running / queued"
              value={`${summary.data.jobs.running ?? 0} / ${summary.data.jobs.queued ?? 0}`}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Services</CardTitle>
            </CardHeader>
            <CardContent>
              {summary.data.providers.length === 0 ? (
                <EmptyState title="No outside calls in this window" />
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH>Host</TH>
                      <TH className="text-right">Calls</TH>
                      <TH className="text-right">Failed</TH>
                      <TH className="text-right">p50</TH>
                      <TH className="text-right">p95</TH>
                      <TH className="text-right">Tokens in/out</TH>
                      <TH className="text-right">Cost</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {summary.data.providers.map((p) => (
                      <TR key={p.host}>
                        <TD className="font-medium">{p.host}</TD>
                        <TD className="text-right tabular-nums">{p.calls}</TD>
                        <TD className="text-right tabular-nums">
                          {p.errors > 0 ? <Badge variant="danger">{p.errors}</Badge> : 0}
                        </TD>
                        <TD className="text-right tabular-nums">{p.p50_ms} ms</TD>
                        <TD className="text-right tabular-nums">{p.p95_ms} ms</TD>
                        <TD className="text-right tabular-nums">
                          {p.tokens_in} / {p.tokens_out}
                        </TD>
                        <TD className="text-right tabular-nums">${p.cost_usd.toFixed(4)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Latest failed calls</CardTitle>
            </CardHeader>
            <CardContent>
              {errors.error && <ErrorState message={errors.error} onRetry={errors.refetch} />}
              {errors.data && errors.data.length === 0 && <EmptyState title="No failed calls" />}
              {errors.data && errors.data.length > 0 && (
                <Table>
                  <THead>
                    <TR>
                      <TH>When</TH>
                      <TH>Call</TH>
                      <TH>Result</TH>
                      <TH>Request id</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {errors.data.map((c) => (
                      <TR key={c.id}>
                        <TD className="whitespace-nowrap text-muted-foreground">{formatDateTime(c.at)}</TD>
                        <TD>
                          <span className="font-medium">{c.method}</span> {c.host}
                          {c.path}
                        </TD>
                        <TD>{c.status ?? c.error ?? "failed"}</TD>
                        <TD className="font-mono text-xs text-muted-foreground">{c.request_id ?? "—"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
