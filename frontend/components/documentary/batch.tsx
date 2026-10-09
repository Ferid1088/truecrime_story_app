"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { Layers, Play } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { CaseListItem, DocumentaryBatchStart, DocumentarySettings } from "@/lib/types";
import { ResolutionBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import { usePolling } from "./hooks";
import {
  DEFAULT_TARGET_MINUTES,
  RunOptionsFields,
  TargetMinutesInput,
  initialRunOptions,
  parseTargetMinutes,
  runOptionsBlockers,
  runOptionsPayload,
} from "./run-options";
import { InlineAlert, JobStatusBadge, isJobActive, jobActivity, jobProgressTone } from "./shared";

const MAX_BATCH_ITEMS = 50;
const BATCH_POLL_MS = 3000;

interface CasePick {
  fromZero: boolean;
  target: string;
}

/** "Several documentaries at once": one job per picked case, run in parallel up to the job limit. */
export function BatchPanel({
  cases,
  settings,
  titles,
  onStarted,
}: {
  cases: CaseListItem[];
  settings: DocumentarySettings;
  titles: Map<number, string>;
  onStarted: () => void;
}) {
  const ids = useId();
  const range = settings.film_minutes;
  const [picks, setPicks] = useState<Record<number, CasePick>>({});
  const [options, setOptions] = useState(() => initialRunOptions(settings));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<DocumentaryBatchStart | null>(null);

  const selected = cases.filter((c) => picks[c.id]);
  const badTargets = selected.filter(
    (c) => picks[c.id].fromZero && parseTargetMinutes(picks[c.id].target, range) == null,
  );
  const blockers = runOptionsBlockers(options, settings);
  if (selected.length === 0) blockers.unshift("Pick at least one case.");
  if (selected.length > MAX_BATCH_ITEMS) blockers.push(`At most ${MAX_BATCH_ITEMS} cases per batch.`);
  if (badTargets.length)
    blockers.push(
      `Target length must be ${range.min}–${range.max} minutes (${badTargets.map((c) => c.title).join(", ")}).`,
    );

  function toggle(c: CaseListItem, on: boolean) {
    setPicks((prev) => {
      const next = { ...prev };
      if (on) next[c.id] = { fromZero: c.story_versions === 0, target: String(DEFAULT_TARGET_MINUTES) };
      else delete next[c.id];
      return next;
    });
  }

  function update(caseId: number, patch: Partial<CasePick>) {
    setPicks((prev) => ({ ...prev, [caseId]: { ...prev[caseId], ...patch } }));
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.startDocumentaryBatch({
        ...runOptionsPayload(options),
        items: selected.map((c) => ({
          case_id: c.id,
          from_zero: picks[c.id].fromZero,
          target_minutes: picks[c.id].fromZero ? parseTargetMinutes(picks[c.id].target, range) : null,
        })),
      });
      setStarted(res);
      setPicks({});
      onStarted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Layers className="size-4" /> Several documentaries at once
        </CardTitle>
        <span className="text-xs text-muted-foreground">
          up to {settings.concurrency.jobs} run in parallel
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs leading-5 text-muted-foreground">
          One production job per case with the same options. Up to {settings.concurrency.jobs} run at the
          same time; the others wait in the queue. A case without a story starts from zero: research,
          then the master story at its target length.
        </p>

        <fieldset>
          <legend className="mb-1.5 flex w-full items-center justify-between text-xs font-medium text-muted-foreground">
            Cases {selected.length > 0 && `· ${selected.length} picked`}
          </legend>
          {cases.length === 0 ? (
            <p className="text-sm text-muted-foreground">No cases yet.</p>
          ) : (
            <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border">
              {cases.map((c) => {
                const pick = picks[c.id];
                return (
                  <li key={c.id} className="px-3 py-2">
                    <div className="flex items-start justify-between gap-2">
                      <Checkbox label={c.title} checked={!!pick} onChange={(e) => toggle(c, e.target.checked)} />
                      <span className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
                        <ResolutionBadge status={c.resolution_status} />
                        {c.story_versions
                          ? `${c.story_versions} stor${c.story_versions === 1 ? "y" : "ies"}`
                          : "no story yet"}
                      </span>
                    </div>
                    {pick && (
                      <div className="mt-2 flex flex-wrap items-start gap-x-4 gap-y-2 pl-5.5">
                        <div className="pt-1">
                          <Checkbox
                            label="From zero (research + master story)"
                            checked={pick.fromZero}
                            onChange={(e) => update(c.id, { fromZero: e.target.checked })}
                          />
                        </div>
                        {pick.fromZero && (
                          <div className="w-36">
                            <TargetMinutesInput
                              id={`${ids}-target-${c.id}`}
                              value={pick.target}
                              onChange={(target) => update(c.id, { target })}
                              range={range}
                              label="Target minutes"
                            />
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </fieldset>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <RunOptionsFields settings={settings} value={options} onChange={setOptions} />
        </div>

        <div className="space-y-2">
          <Button onClick={start} loading={busy} disabled={blockers.length > 0 || busy}>
            <Play className="size-4" />
            {selected.length === 1 ? "Start 1 documentary" : `Start ${selected.length} documentaries`}
          </Button>
          {blockers.length > 0 && (
            <ul className="space-y-1 text-[11px] leading-4 text-muted-foreground" aria-live="polite">
              {blockers.map((b) => (
                <li key={b}>· {b}</li>
              ))}
            </ul>
          )}
          {error && <InlineAlert tone="error">{error}</InlineAlert>}
        </div>

        {started && (
          <div className="space-y-3 border-t border-border pt-4">
            {started.rejected.length > 0 && (
              <InlineAlert tone="warning">
                <p className="font-medium">
                  {started.rejected.length} case{started.rejected.length === 1 ? "" : "s"} could not start:
                </p>
                <ul className="mt-1 space-y-0.5">
                  {started.rejected.map((r) => (
                    <li key={r.case_id}>
                      <span className="font-medium">{titles.get(r.case_id) ?? `Case #${r.case_id}`}:</span>{" "}
                      {r.reason}
                    </li>
                  ))}
                </ul>
              </InlineAlert>
            )}
            <BatchProgress
              batchId={started.batch_id}
              titles={titles}
              maxParallel={started.max_parallel_jobs}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function BatchProgress({
  batchId,
  titles,
  maxParallel,
}: {
  batchId: string;
  titles: Map<number, string>;
  maxParallel: number;
}) {
  const { data, error, refetch } = useApi(() => api.documentaryBatch(batchId), [batchId], {
    keepPrevious: true,
  });
  const active = !data || data.jobs.some(isJobActive);
  usePolling(refetch, active, BATCH_POLL_MS);

  if (!data) return error ? <ErrorState message={error} onRetry={refetch} /> : <Skeleton className="h-24" />;
  const percent = Math.round(data.progress * 100);
  const counts = Object.entries(data.statuses)
    .map(([status, n]) => `${n} ${status}`)
    .join(" · ");

  return (
    <section aria-labelledby={`batch-${batchId}`} className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
        <h3 id={`batch-${batchId}`} className="font-medium">
          Batch <span className="font-mono">{batchId}</span>
        </h3>
        <span className="text-muted-foreground">
          {counts} · {maxParallel} at a time
        </span>
      </div>
      <div role="progressbar" aria-label="Batch progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <Progress value={percent} />
      </div>
      {error && <InlineAlert tone="warning">Lost contact while following the batch — retrying. ({error})</InlineAlert>}
      <ul className="divide-y divide-border rounded-md border border-border">
        {data.jobs.map((j) => {
          const errors = Object.entries(j.result.errors ?? {});
          return (
            <li key={j.id} className="px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Link href={`/documentary/${j.case_id}`} className="min-w-0 truncate font-medium text-primary hover:underline">
                  {titles.get(j.case_id) ?? `Case #${j.case_id}`}
                </Link>
                <span className="flex items-center gap-2">
                  <span className="tabular-nums text-muted-foreground">{Math.round(j.progress * 100)}%</span>
                  <JobStatusBadge status={j.status} />
                </span>
              </div>
              <Progress value={j.progress * 100} className="mt-1.5 h-1" barClassName={jobProgressTone(j.status)} />
              <p className="mt-1 break-words text-muted-foreground">{jobActivity(j)}</p>
              {errors.map(([lang, message]) => (
                <p key={lang} className="mt-0.5 break-words text-rose-600 dark:text-rose-400">
                  {langLabel(lang)}: {message}
                </p>
              ))}
              {!errors.length && j.status === "failed" && j.error && (
                <p className="mt-0.5 break-words text-rose-600 dark:text-rose-400">{j.error}</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
