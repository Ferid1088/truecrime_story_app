"use client";

import { useEffect, useRef, useState } from "react";
import { Play, Radar } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import type { MonitorRun, MonitorStatus } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Collapsible } from "./shared";

const POLL_MS = 3000;
const RUN_TIMEOUT_MS = 15 * 60 * 1000;

function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

function runSummary(r: MonitorRun): string {
  if (r.status === "running") return `running · ${r.cases_checked} case${r.cases_checked === 1 ? "" : "s"} checked so far`;
  if (r.status === "failed") return `failed${r.error ? `: ${r.error}` : ""}`;
  return (
    `${r.cases_checked} case${r.cases_checked === 1 ? "" : "s"} checked · ${r.deep_checks} deep · ` +
    `${r.status_changes} status change${r.status_changes === 1 ? "" : "s"} · ` +
    `${r.follow_ups_created} follow-up${r.follow_ups_created === 1 ? "" : "s"}`
  );
}

/**
 * The unsolved-case monitor at a glance: last run, next run, watched
 * cases, recent runs, and "Run monitor now" (followed until it finishes).
 */
export function MonitorStatusCard({ onRunFinished }: { onRunFinished?: () => void }) {
  const { data, error, refetch } = useApi(() => api.monitorStatus(), []);
  const [polled, setPolled] = useState<MonitorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  const status = polled ?? data;
  const latest = status?.runs[0] ?? null;
  const running = busy || latest?.status === "running";

  async function runNow() {
    setBusy(true);
    setRunError(null);
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    const before = status?.runs[0]?.id ?? null;
    try {
      await api.runMonitor();
      const deadline = Date.now() + RUN_TIMEOUT_MS;
      for (;;) {
        await sleep(POLL_MS, ctl.signal);
        const next = await api.monitorStatus();
        setPolled(next);
        const run = next.runs[0];
        if (run && run.id !== before && run.status !== "running") break;
        if (Date.now() > deadline) {
          setRunError("The monitor run is taking long — it continues in the background.");
          break;
        }
      }
      onRunFinished?.();
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setRunError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (!ctl.signal.aborted) setBusy(false);
    }
  }

  if (!status) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 py-3 text-sm text-muted-foreground">
          <Radar className="size-4" aria-hidden />
          {error ? (
            <>
              <span className="min-w-0 break-words">Unsolved-case monitor unavailable: {error}</span>
              <Button variant="ghost" size="sm" onClick={refetch}>
                Retry
              </Button>
            </>
          ) : (
            "Loading the unsolved-case monitor…"
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-2 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="flex items-center gap-1.5 font-medium">
              <Radar className="size-4 text-muted-foreground" aria-hidden /> Unsolved-case monitor
            </span>
            {!status.enabled && <Badge variant="outline">disabled</Badge>}
            {running && (
              <Badge variant="warning" aria-live="polite">
                <span className="size-1.5 animate-pulse rounded-full bg-amber-500" aria-hidden />
                running
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">
              Last run{" "}
              <span className="text-foreground">{status.last_run_at ? formatDateTime(status.last_run_at) : "never"}</span>
            </span>
            <span className="text-xs text-muted-foreground">
              Next run{" "}
              <span className="text-foreground">
                {status.enabled ? (status.next_run_at ? formatDateTime(status.next_run_at) : "—") : "off"}
              </span>
            </span>
            <span className="text-xs text-muted-foreground">
              Watching{" "}
              <span className="font-medium tabular-nums text-foreground">{status.watched_cases}</span> unsolved /
              under-review case{status.watched_cases === 1 ? "" : "s"} · every {status.interval_hours} h
              {!status.running_in_process && status.enabled && " · scheduler not running in this API process"}
            </span>
          </div>
          <Button size="sm" variant="secondary" onClick={runNow} loading={running} disabled={running}>
            {!running && <Play className="size-3.5" />} Run monitor now
          </Button>
        </div>
        {latest && (
          <p className="text-xs text-muted-foreground">
            Latest: {humanize(latest.trigger)} run · {runSummary(latest)} · {latest.search_calls} searches ·{" "}
            {latest.fetch_calls} fetches · {latest.llm_calls} LLM calls
          </p>
        )}
        {runError && <p className="text-xs text-rose-500">{runError}</p>}
        {status.runs.length > 0 && (
          <Collapsible title="Monitor history" count={status.runs.length}>
            <div className="-mx-4">
              <Table className="text-xs">
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Started</TH>
                    <TH>Trigger</TH>
                    <TH>Status</TH>
                    <TH className="text-right">Cases</TH>
                    <TH className="text-right">Deep</TH>
                    <TH className="text-right">Changes</TH>
                    <TH className="text-right">Follow-ups</TH>
                    <TH className="text-right">Search / fetch / LLM</TH>
                  </TR>
                </THead>
                <TBody>
                  {status.runs.map((r) => (
                    <TR key={r.id}>
                      <TD className="whitespace-nowrap">{formatDateTime(r.started_at)}</TD>
                      <TD>{humanize(r.trigger)}</TD>
                      <TD>
                        <Badge
                          variant={r.status === "completed" ? "success" : r.status === "failed" ? "danger" : "warning"}
                          title={r.error ?? undefined}
                        >
                          {r.status}
                        </Badge>
                      </TD>
                      <TD className="text-right tabular-nums">{r.cases_checked}</TD>
                      <TD className="text-right tabular-nums">{r.deep_checks}</TD>
                      <TD className="text-right tabular-nums">{r.status_changes}</TD>
                      <TD className="text-right tabular-nums">{r.follow_ups_created}</TD>
                      <TD className="text-right tabular-nums">
                        {r.search_calls} / {r.fetch_calls} / {r.llm_calls}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          </Collapsible>
        )}
      </CardContent>
    </Card>
  );
}
