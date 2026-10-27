"use client";

import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, PenLine, Play, RotateCcw, Square } from "lucide-react";
import { api, mvpApi } from "@/lib/api";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { CaseCostPanel, CostReport, DocumentaryDryRunResult, DocumentaryJob, FilmMinutesRange } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState } from "@/components/state";
import { cn } from "@/lib/utils";
import { ProductionTypeBadge } from "@/components/lifecycle/shared";
import {
  DEFAULT_TARGET_MINUTES,
  RunOptionsFields,
  TargetMinutesInput,
  initialRunOptions,
  parseTargetMinutes,
  runOptionsBlockers,
  runOptionsPayload,
} from "../run-options";
import {
  type DocumentaryTabProps,
  InlineAlert,
  JobStatusBadge,
  filmLengthIssue,
  isJobActive,
  jobActivity,
  jobProgressTone,
  stageLabel,
  WarningList,
} from "../shared";

interface RunTabProps extends Omit<DocumentaryTabProps, "refreshKey"> {
  finalId: number | null;
  job: DocumentaryJob | null;
  onFinalChange: (versionId: number) => void;
  /** Follow this job (just started, cancelled or resumed). */
  onJobChange: (job: DocumentaryJob) => void;
  onRefresh: () => void;
}

function modeLabel(job: DocumentaryJob): string {
  const mode = job.mode === "pilot" ? `Pilot ${formatTimecode(job.pilot_seconds)}` : "Full film";
  return job.from_zero ? `${mode} · from zero` : mode;
}

export function RunTab({
  caseId,
  settings,
  overview,
  finalId,
  job,
  onFinalChange,
  onJobChange,
  onRefresh,
}: RunTabProps) {
  const router = useRouter();
  const range = settings.film_minutes;
  const [options, setOptions] = useState(() => initialRunOptions(settings));
  const [fromZeroChoice, setFromZeroChoice] = useState(false);
  const [targetMinutes, setTargetMinutes] = useState(String(DEFAULT_TARGET_MINUTES));
  const [busy, setBusy] = useState<"start" | "cancel" | "resume" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewJobId, setViewJobId] = useState<number | null>(null);
  const [estimate, setEstimate] = useState<DocumentaryDryRunResult | null>(null);
  const [costs, setCosts] = useState<CaseCostPanel | null>(null);
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const ids = useId();

  const noFinal = overview.finals.length === 0;
  const fromZero = noFinal || fromZeroChoice;
  const target = parseTargetMinutes(targetMinutes, range);
  const outOfRange = options.languages.filter((l) =>
    filmLengthIssue(overview.languages[l]?.estimated_film_minutes, range),
  );

  const blockers = runOptionsBlockers(options, settings);
  if (!fromZero && finalId == null) blockers.unshift("Choose a final version.");
  const unapproved = options.languages.filter((l) => {
    const f = overview.finals.find((x) => x.language === l);
    return !fromZero && (!f || !f.approved_at);
  });
  if (unapproved.length)
    blockers.push(`The final version of ${unapproved.map(langLabel).join(", ")} is not approved yet (approve it in the Story Studio).`);
  if (fromZero && target == null) blockers.push(`Target length must be ${range.min}–${range.max} minutes.`);
  if (!fromZero && options.mode === "full" && outOfRange.length)
    blockers.push(
      `A full film needs every language between ${range.min} and ${range.max} minutes — ${outOfRange
        .map(langLabel)
        .join(", ")} ${outOfRange.length === 1 ? "falls" : "fall"} outside. Run a pilot, or run the language pipeline for a longer film.`,
    );
  if (isJobActive(job)) blockers.push(`Job #${job?.id} is still running.`);
  const pilotLength = Number(options.pilotSeconds);
  const languageKey = options.languages.join(",");
  useEffect(() => {
    if (options.mode !== "pilot" || !Number.isFinite(pilotLength) || pilotLength < 11 || pilotLength > 1800) {
      return;
    }
    let cancelled = false;
    api.documentaryDryRun(caseId, options.languages, pilotLength)
      .then((next) => { if (!cancelled) setEstimate(next); })
      .catch(() => { if (!cancelled) setEstimate(null); });
    return () => { cancelled = true; };
  }, [caseId, options.mode, options.pilotSeconds, pilotLength, languageKey, options.languages]);

  useEffect(() => {
    let cancelled = false;
    api.caseCosts(caseId)
      .then((next) => { if (!cancelled) setCosts(next); })
      .catch(() => { if (!cancelled) setCosts(null); });
    mvpApi.candidates(caseId, "needs_review")
      .then((next) => { if (!cancelled) setReviewCount(next.length); })
      .catch(() => { if (!cancelled) setReviewCount(null); });
    return () => { cancelled = true; };
  }, [caseId]);

  const shown =
    viewJobId != null && viewJobId !== job?.id
      ? (overview.jobs.find((j) => j.id === viewJobId) ?? job)
      : job;
  const history = overview.jobs.map((j) => (job && j.id === job.id ? job : j));

  async function act(kind: "start" | "cancel" | "resume", run: () => Promise<DocumentaryJob>) {
    setBusy(kind);
    setError(null);
    try {
      const next = await run();
      setViewJobId(null);
      onJobChange(next);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  // One documentary per language, each from that language's own final version.
  const start = () =>
    act("start", async () => {
      const { languages, ...rest } = runOptionsPayload(options);
      const jobs = await Promise.all(
        languages.map((language) =>
          api.startDocumentaryJob(caseId, {
            ...rest,
            language,
            final_version_id: fromZero
              ? null
              : (overview.finals.find((f) => f.language === language && f.id === finalId)?.id ??
                 overview.finals.find((f) => f.language === language)?.id ?? null),
            from_zero: fromZero,
            target_minutes: fromZero ? target : null,
          }),
        ),
      );
      return jobs[0];
    });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr]">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>New production run</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-xs leading-5 text-muted-foreground">
              Every finished documentary runs{" "}
              <span className="font-medium text-foreground">
                {range.min}–{range.max} minutes
              </span>
              . A pilot sets the research notes, final text, narration, performance, pictures,
              music and Shorts selection to its selected length before producing the whole film.
            </p>

            {noFinal ? (
              <div className="rounded-md border border-border bg-subtle px-3 py-2 text-xs leading-5">
                <p className="font-medium">No final version yet</p>
                <p className="text-muted-foreground">
                  This run starts from zero: it researches the case and runs the language pipeline
                  (one shared draft, then a native final text per language) first. The final text of
                  each language still needs your approval before its film is produced.
                </p>
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto"
                  onClick={() => router.push(`/story-studio?case=${caseId}`)}
                >
                  <PenLine className="size-3.5" /> Open Story Studio
                </Button>
              </div>
            ) : (
              <div>
                <label htmlFor={`${ids}-final`} className="mb-1 block text-xs font-medium text-muted-foreground">
                  Final version
                </label>
                <Select
                  id={`${ids}-final`}
                  value={finalId ?? ""}
                  disabled={fromZero}
                  onChange={(e) => onFinalChange(Number(e.target.value))}
                >
                  {finalId == null && (
                    <option value="" disabled>
                      Choose a final version…
                    </option>
                  )}
                  {overview.finals.map((m) => (
                    <option key={m.id} value={m.id}>
                      v{m.version} · {langLabel(m.language)} · {m.words.toLocaleString()} words ·{" "}
                      {humanize(m.status)}{m.approved_at ? " · approved" : " · awaiting approval"}
                    </option>
                  ))}
                </Select>
              </div>
            )}

            <div className="space-y-2">
              <Checkbox
                label="Start from zero (research + the language pipeline)"
                checked={fromZero}
                disabled={noFinal}
                onChange={(e) => setFromZeroChoice(e.target.checked)}
                aria-describedby={`${ids}-zero-hint`}
              />
              <p id={`${ids}-zero-hint`} className="text-[11px] leading-4 text-muted-foreground">
                Research runs only when the case has no facts yet; existing final texts are
                reused instead of written again.
              </p>
              {fromZero && (
                <TargetMinutesInput
                  id={`${ids}-target`}
                  value={targetMinutes}
                  onChange={setTargetMinutes}
                  range={range}
                  label="Film length (minutes)"
                />
              )}
            </div>

            {fromZero && overview.research_preflight?.needs_details && (
              <div className="rounded-md border border-amber-400/50 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200" data-testid="research-preflight-warning">
                <p className="font-medium">{overview.research_preflight.warning}</p>
                <p className="mt-1 font-medium">Search queries</p>
                <ul className="list-disc pl-4">
                  {overview.research_preflight.queries.map((query) => <li key={query}>{query}</li>)}
                </ul>
              </div>
            )}

            <RunOptionsFields settings={settings} value={options} onChange={setOptions} />

            {options.mode === "pilot" && Number.isFinite(pilotLength) && pilotLength >= 11 && pilotLength <= 1800 && estimate && (
              <EstimateCard estimate={estimate} />
            )}

            <BudgetSummary costs={costs} estimate={estimate} />

            <div className="flex gap-2">
              <Button
                className="flex-1"
                onClick={start}
                loading={busy === "start"}
                disabled={blockers.length > 0 || busy !== null}
              >
                <Play className="size-4" /> {options.mode === "pilot" ? "Start pilot" : "Start full film"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => router.push(`/documentary/${caseId}/review`)}
                data-testid="review-pictures-button"
              >
                Review pictures{reviewCount != null ? ` (${reviewCount})` : ""}
              </Button>
            </div>
            {blockers.length > 0 && (
              <ul className="space-y-1 text-[11px] leading-4 text-muted-foreground" aria-live="polite">
                {blockers.map((b) => (
                  <li key={b}>· {b}</li>
                ))}
              </ul>
            )}
            {error && <InlineAlert tone="error">{error}</InlineAlert>}
          </CardContent>
        </Card>

        <FilmLengthCard
          languages={settings.languages}
          estimates={Object.fromEntries(
            settings.languages.map((l) => [l, overview.languages[l]?.estimated_film_minutes ?? null]),
          )}
          range={range}
        />
      </div>

      <div className="min-w-0 space-y-4">
        {shown ? (
          <JobPanel
            job={shown}
            isCurrent={shown.id === job?.id}
            busy={busy}
            onBack={() => setViewJobId(null)}
            onCancel={() => act("cancel", () => api.cancelDocumentaryJob(shown.id))}
            onResume={() => act("resume", () => api.resumeDocumentaryJob(shown.id))}
          />
        ) : (
          <EmptyState
            title="No production run yet"
            description="Start a pilot to produce the opening of the film in every language. Progress and every stage appear here."
          />
        )}

        {history.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Job history</CardTitle>
              <span className="text-xs text-muted-foreground">last {history.length}</span>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Job</TH>
                    <TH>Started</TH>
                    <TH>Mode</TH>
                    <TH>Languages</TH>
                    <TH>Profile</TH>
                    <TH>Status</TH>
                    <TH className="text-right">Progress</TH>
                  </TR>
                </THead>
                <TBody>
                  {history.map((j) => (
                    <TR key={j.id} className={cn(shown?.id === j.id && "bg-accent/40")}>
                      <TD>
                        <button
                          type="button"
                          onClick={() => setViewJobId(j.id)}
                          aria-label={`Show job ${j.id}`}
                          aria-current={shown?.id === j.id ? "true" : undefined}
                          className="cursor-pointer font-medium text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/50"
                        >
                          #{j.id}
                        </button>
                      </TD>
                      <TD className="whitespace-nowrap text-muted-foreground">{formatDateTime(j.created_at)}</TD>
                      <TD className="whitespace-nowrap">
                        {modeLabel(j)}
                        {j.production_type === "follow_up" && (
                          <span className="ml-1.5 align-middle">
                            <ProductionTypeBadge type={j.production_type} />
                          </span>
                        )}
                      </TD>
                      <TD className="text-muted-foreground">{j.languages.map((l) => l.toUpperCase()).join(" ")}</TD>
                      <TD className="text-muted-foreground">{j.render_profile}</TD>
                      <TD>
                        <JobStatusBadge status={j.status} />
                      </TD>
                      <TD className="text-right tabular-nums">{Math.round(j.progress * 100)}%</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function money(value: number): string {
  return value < 0.01 ? `€${value.toFixed(4)}` : `€${value.toFixed(2)}`;
}

function EstimateCard({ estimate }: { estimate: DocumentaryDryRunResult }) {
  return (
    <div className="rounded-md border border-sky-500/30 bg-sky-500/5 px-3 py-2.5" data-testid="pilot-estimate">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs font-semibold">No-send estimate · {estimate.pilot_seconds}s pilot</p>
        <span className="text-[10px] text-muted-foreground">prices {estimate.pricing_last_verified}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Low {money(estimate.estimate_range_eur.low)} · expected {money(estimate.estimate_range_eur.expected)} · high {money(estimate.estimate_range_eur.high)}
      </p>
      <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
        Tokens are input/output separately: research source text → notes; final-text input = notes → the selected pilot length;
        spoken story × 1.2 → × 1.05; performance story × 1.1 → output scaled to that length.
      </p>
      {estimate.price_warnings.length > 0 && (
        <div className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300" role="alert">
          <p className="font-semibold">Verify before paying</p>
          {estimate.price_warnings.map((warning) => (
            <p key={`${warning.stage}-${warning.reason}`}>{warning.stage}: {warning.reason}</p>
          ))}
        </div>
      )}
      <div className="mt-2 overflow-x-auto">
        <Table>
          <THead><TR className="hover:bg-transparent"><TH>Stage</TH><TH>Low</TH><TH>Expected</TH><TH>High</TH></TR></THead>
          <TBody>{estimate.stages.map((row) => (
            <TR key={row.stage} className="text-[11px]">
              <TD className="whitespace-nowrap font-medium">{row.stage}{row.price_status === "unverified" && " · verify"}</TD>
              <TD>{money(row.eur.low)}</TD><TD>{money(row.eur.expected)}</TD><TD>{money(row.eur.high)}</TD>
            </TR>
          ))}</TBody>
        </Table>
      </div>
    </div>
  );
}

function BudgetSummary({ costs, estimate }: { costs: CaseCostPanel | null; estimate: DocumentaryDryRunResult | null }) {
  const warnings = estimate?.price_warnings ?? [];
  return (
    <div className="rounded-md border border-border bg-subtle px-3 py-2.5" data-testid="preflight-budget">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-semibold">Budget gate</p>
        <span className="text-[11px] tabular-nums">cap {costs ? money(costs.cap_eur) : "—"}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Spent so far {costs ? money(costs.spent_eur) : "—"} · remaining {costs ? money(costs.remaining_eur) : "—"}
      </p>
      <div className="mt-1 text-[11px]" role="status">
        <span className="font-medium">Warnings:</span>{" "}
        {warnings.length === 0 ? "none" : warnings.map((warning) => `${warning.stage}: ${warning.reason}`).join("; ")}
      </div>
      {costs?.stop_reason && (
        <p className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">Budget status: {costs.stop_reason}</p>
      )}
    </div>
  );
}

function FilmLengthCard({
  languages,
  estimates,
  range,
}: {
  languages: string[];
  estimates: Record<string, number | null>;
  range: FilmMinutesRange;
}) {
  const scaleMax = range.max * 1.25;
  const pct = (m: number) => `${(Math.min(m, scaleMax) / scaleMax) * 100}%`;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Estimated film length</CardTitle>
        <span className="text-xs text-muted-foreground tabular-nums">
          allowed {range.min}–{range.max} min
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        {languages.map((l) => {
          const minutes = estimates[l];
          const issue = filmLengthIssue(minutes, range);
          return (
            <div key={l}>
              <div className="mb-1 flex items-baseline justify-between text-xs">
                <span>{langLabel(l)}</span>
                <span
                  className={cn(
                    "font-medium tabular-nums",
                    issue && "text-rose-600 dark:text-rose-400",
                  )}
                >
                  {minutes == null ? "—" : `${minutes.toFixed(1)} min`}
                </span>
              </div>
              <div className="relative h-1.5 rounded-full bg-muted" aria-hidden>
                <div
                  className="absolute inset-y-0 rounded-full bg-emerald-500/25"
                  style={{ left: pct(range.min), width: `calc(${pct(range.max)} - ${pct(range.min)})` }}
                />
                {minutes != null && (
                  <div
                    className={cn(
                      "absolute -top-0.5 h-2.5 w-1 -translate-x-1/2 rounded-full",
                      issue ? "bg-rose-500" : "bg-emerald-500",
                    )}
                    style={{ left: pct(minutes) }}
                  />
                )}
              </div>
              {issue && (
                <p className="mt-1 flex items-start gap-1 text-[11px] leading-4 text-rose-600 dark:text-rose-400">
                  <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden />
                  {issue === "short"
                    ? `Story too short for a full film (needs at least ${range.min} min). Pilots are still allowed.`
                    : `Longer than ${range.max} min — too long for one film. Pilots are still allowed.`}
                </p>
              )}
            </div>
          );
        })}
        <p className="text-[11px] leading-4 text-muted-foreground">
          Estimated from each spoken version: its words at the measured narration speed of the
          language, plus planned breaths and music moments. Shown once the spoken version exists.
        </p>
      </CardContent>
    </Card>
  );
}

function JobPanel({
  job,
  isCurrent,
  busy,
  onBack,
  onCancel,
  onResume,
}: {
  job: DocumentaryJob;
  isCurrent: boolean;
  busy: "start" | "cancel" | "resume" | null;
  onBack: () => void;
  onCancel: () => void;
  onResume: () => void;
}) {
  const router = useRouter();
  const percent = Math.round(job.progress * 100);
  const canCancel = job.status === "queued" || job.status === "running";
  const canResume =
    job.status === "failed" ||
    job.status === "cancelled" ||
    job.status === "partial" ||
    job.status === "interrupted";
  const resumeDisabled = canResume && job.resume_allowed === false;
  const languageErrors = Object.entries(job.result.errors ?? {});
  const approvalStage = job.stages.find((stage) => stage.name === "final_approval");
  const approvalDetail = approvalStage?.detail && typeof approvalStage.detail === "object"
    ? approvalStage.detail as Record<string, unknown>
    : null;
  const approvedBy = typeof approvalDetail?.approved_by === "string" ? approvalDetail.approved_by : null;
  const callProgress = job.result.call_progress;

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle className="flex items-center gap-2">
          Job #{job.id} <JobStatusBadge status={job.status} />
          <ProductionTypeBadge type={job.production_type} />
          {!isCurrent && <span className="text-xs font-normal text-muted-foreground">(earlier run)</span>}
        </CardTitle>
        <div className="flex flex-wrap gap-2">
          {!isCurrent && (
            <Button variant="ghost" size="sm" onClick={onBack}>
              Back to current
            </Button>
          )}
          {canCancel && (
            <Button variant="outline" size="sm" onClick={onCancel} loading={busy === "cancel"} disabled={busy !== null}>
              <Square className="size-3.5" /> Cancel job
            </Button>
          )}
          {canResume && (
            <Button variant="secondary" size="sm" onClick={onResume} loading={busy === "resume"} disabled={busy !== null || resumeDisabled} title={resumeDisabled ? (job.resume_disabled_reason ?? "Resume is unavailable") : undefined}>
              <RotateCcw className="size-3.5" /> Resume
            </Button>
          )}
          {resumeDisabled && <span className="self-center text-[11px] text-muted-foreground">{job.resume_disabled_reason}</span>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {job.production_type === "follow_up" && (
          <p className="rounded-md border border-indigo-500/20 bg-indigo-500/5 px-3 py-2 text-xs leading-5">
            Update video (approved follow-up{job.follow_up_id != null ? ` #${job.follow_up_id}` : ""}): research
            refreshes the new developments first, and the film opens with the earlier episode and says the case is
            now solved.
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {job.mode === "pilot" ? `Pilot · opening ${formatTimecode(job.pilot_seconds)}` : "Full film"}
          {job.from_zero &&
            ` · from zero${job.target_minutes ? ` (final text ${job.target_minutes} min)` : ""}`}{" "}
          · {job.languages.map(langLabel).join(", ")} · {job.render_profile} profile · started{" "}
          {formatDateTime(job.created_at)}
          {job.completed_at && ` · finished ${formatDateTime(job.completed_at)}`}
        </p>
        <div>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
            <span aria-live="polite">{jobActivity(job)}</span>
            <span className="font-medium tabular-nums">{percent}%</span>
          </div>
          <div
            role="progressbar"
            aria-label={`Job ${job.id} progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <Progress value={percent} barClassName={jobProgressTone(job.status)} />
          </div>
        </div>
        {callProgress?.active && (
          <div
            className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs"
            data-testid="provider-call-progress"
            aria-live="polite"
          >
            <span className="font-medium">{stageLabel(callProgress.stage)}:</span>{" "}
            call {callProgress.call_number} · {callProgress.provider} · {callProgress.elapsed_seconds.toFixed(1)}s elapsed
            {callProgress.stage_budget_seconds != null &&
              ` · stage budget ${callProgress.stage_budget_seconds.toFixed(1)}s`}
          </div>
        )}
        {languageErrors.length > 0 ? (
          <InlineAlert tone={job.status === "partial" ? "warning" : "error"}>
            <p className="font-medium">
              {languageErrors.length === 1 ? "One language failed" : `${languageErrors.length} languages failed`}
              {job.status === "partial" && " — the other languages finished. Resume retries the failed ones."}
            </p>
            <ul className="mt-1 space-y-0.5">
              {languageErrors.map(([lang, message]) => (
                <li key={lang} className="break-words">
                  <span className="font-medium">{langLabel(lang)}:</span> {message}
                </li>
              ))}
            </ul>
          </InlineAlert>
        ) : (
          job.error && (
            <InlineAlert tone={job.status === "interrupted" ? "warning" : "error"}>
              <span className="font-medium">Error:</span> {job.error}
            </InlineAlert>
          )
        )}
        {job.next_action && (
          <p className="rounded-md border border-sky-500/30 bg-sky-500/5 px-3 py-2 text-xs">
            <span className="font-medium">Next:</span> {job.next_action}
          </p>
        )}
        {job.error?.includes("Awaiting picture review") && (
          <Button size="sm" onClick={() => router.push(`/documentary/${job.case_id}/review`)} data-testid="job-review-pictures">
            Review pictures
          </Button>
        )}
        {approvalStage?.status === "done" && approvedBy && (
          <div className="rounded-md border border-border px-3 py-2 text-xs leading-5" data-testid="final-approval">
            <span className="font-medium">Final version approved</span> by {approvedBy}.
          </div>
        )}
        <WarningList warnings={job.result.warnings ?? []} />
        {job.result.cost_report && <CostReportCard report={job.result.cost_report} />}
      </CardContent>
    </Card>
  );
}

function CostReportCard({ report }: { report: CostReport }) {
  return (
    <div className="rounded-md border border-border" data-testid="cost-report">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-3 py-2">
        <p className="text-xs font-semibold">Estimate vs actual</p>
        <p className="text-[11px] text-muted-foreground tabular-nums">
          estimated {money(report.totals.estimated_eur)} · actual {money(report.totals.actual_eur)}
        </p>
      </div>
      <div className="overflow-x-auto">
        <Table>
          <THead><TR className="hover:bg-transparent"><TH>Stage</TH><TH>Estimated EUR</TH><TH>Actual EUR</TH><TH>Difference</TH><TH>Flag</TH></TR></THead>
          <TBody>{report.rows.map((row) => (
            <TR key={row.stage} className="text-[11px]">
              <TD className="whitespace-nowrap font-medium">{row.stage}</TD>
              <TD>{money(row.estimated_eur)}</TD><TD>{row.status === "not run" ? "not run" : money(row.actual_eur ?? 0)}</TD>
              <TD>{row.status === "not run" ? "not run" : row.difference_percent == null ? "—" : `${row.difference_percent.toFixed(1)}%`}</TD>
              <TD>{row.estimated ? <span className="text-amber-700 dark:text-amber-400" title={row.estimated_reason ?? undefined}>estimated</span> : row.status === "not run" ? "not run" : "reported"}</TD>
            </TR>
          ))}</TBody>
        </Table>
      </div>
    </div>
  );
}
