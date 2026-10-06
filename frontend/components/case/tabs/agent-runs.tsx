"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, formatDuration } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { RunStatusBadge } from "@/components/status-badge";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

export function AgentRunsTab({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data: runs, error, loading, refetch } = useApi(
    () => api.caseAgentRuns(caseId),
    [caseId, refreshKey],
  );

  if (loading) return <TableSkeleton rows={5} cols={5} />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!runs || runs.length === 0)
    return (
      <EmptyState
        title="No agent runs"
        description="Every discovery, research and writing step is recorded here."
      />
    );

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <THead>
            <TR className="hover:bg-transparent">
              <TH>Agent</TH>
              <TH>Status</TH>
              <TH>Model</TH>
              <TH>Started</TH>
              <TH className="text-right">Duration</TH>
              <TH>Input</TH>
              <TH>Output</TH>
              <TH>Error</TH>
            </TR>
          </THead>
          <TBody>
            {runs.map((r) => (
              <TR key={r.id}>
                <TD className="font-medium">{r.agent_name}</TD>
                <TD>
                  <RunStatusBadge status={r.status} />
                </TD>
                <TD className="max-w-48">
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">
                    {r.model ?? r.provider ?? "—"}
                    {r.temperature != null ? ` · t=${r.temperature}` : ""}
                    {r.fallback_used ? " · fb" : ""}
                  </span>
                  {r.total_tokens != null && (
                    <span className="block truncate text-[10px] text-muted-foreground/60">
                      {r.total_tokens.toLocaleString()} tok
                      {r.estimated_cost_usd != null ? ` · $${r.estimated_cost_usd.toFixed(4)}` : ""}
                    </span>
                  )}
                  {r.role && (
                    <span className="block truncate text-[10px] text-muted-foreground/60">
                      {r.role}
                    </span>
                  )}
                </TD>
                <TD className="whitespace-nowrap text-muted-foreground">{formatDateTime(r.started_at)}</TD>
                <TD className="text-right tabular-nums">{formatDuration(r.duration_ms)}</TD>
                <TD className="max-w-48">
                  <span className="block truncate text-xs text-muted-foreground">{r.input_summary ?? "—"}</span>
                </TD>
                <TD className="max-w-48">
                  <span className="block truncate text-xs text-muted-foreground">{r.output_summary ?? "—"}</span>
                </TD>
                <TD className="max-w-48">
                  <span className="block truncate text-xs text-rose-500">{r.error ?? "—"}</span>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
