"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, PenLine, Play, RotateCcw, Square } from "lucide-react";
import { api } from "@/lib/api";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { DocumentaryJob, FilmMinutesRange } from "@/lib/types";
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
  StageIcon,
  filmLengthIssue,
  isJobActive,
  jobActivity,
  jobProgressTone,
  stageLabel,
  summarizeDetail,
} from "../shared";

interface RunTabProps extends Omit<DocumentaryTabProps, "refreshKey"> {
  masterId: number | null;
  job: DocumentaryJob | null;
  onMasterChange: (versionId: number) => void;
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
  masterId,
  job,
  onMasterChange,
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
  const ids = useId();

  const noMaster = overview.masters.length === 0;
  const fromZero = noMaster || fromZeroChoice;
  const target = parseTargetMinutes(targetMinutes, range);
  const outOfRange = options.languages.filter((l) =>
    filmLengthIssue(overview.languages[l]?.estimated_film_minutes, range),
  );

  const blockers = runOptionsBlockers(options, settings);
  if (!fromZero && masterId == null) blockers.unshift("Choose a master story.");
  if (fromZero && target == null) blockers.push(`Target length must be ${range.min}–${range.max} minutes.`);
  if (!fromZero && options.mode === "full" && outOfRange.length)
    blockers.push(
      `A full film needs every language between ${range.min} and ${range.max} minutes — ${outOfRange
        .map(langLabel)
        .join(", ")} ${outOfRange.length === 1 ? "falls" : "fall"} outside. Run a pilot, or write a longer master story.`,
    );
  if (isJobActive(job)) blockers.push(`Job #${job?.id} is still running.`);

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

  const start = () =>
    act("start", () =>
      api.startDocumentaryJob(caseId, {
        ...runOptionsPayload(options),
        master_version_id: fromZero ? null : masterId,
        from_zero: fromZero,
        target_minutes: fromZero ? target : null,
      }),
    );

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
              . A pilot renders only the opening of that full-length film — narration, pictures,
              music and subtitles — so you can judge it before producing the whole film.
            </p>

            {noMaster ? (
              <div className="rounded-md border border-border bg-subtle px-3 py-2 text-xs leading-5">
                <p className="font-medium">No master story yet</p>
                <p className="text-muted-foreground">
                  This run starts from zero: it researches the case and writes the English master story
                  at the target length first. You can also write it yourself.
                </p>
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto"
                  onClick={() => router.push(`/studio?case=${caseId}`)}
                >
                  <PenLine className="size-3.5" /> Open Story Studio
                </Button>
              </div>
            ) : (
              <div>
                <label htmlFor={`${ids}-master`} className="mb-1 block text-xs font-medium text-muted-foreground">
                  Master story
                </label>
                <Select
                  id={`${ids}-master`}
                  value={masterId ?? ""}
                  disabled={fromZero}
                  onChange={(e) => onMasterChange(Number(e.target.value))}
                >
                  {masterId == null && (
                    <option value="" disabled>
                      Choose a master story…
                    </option>
                  )}
                  {overview.masters.map((m) => (
                    <option key={m.id} value={m.id}>
                      v{m.version} · {langLabel(m.language)} · {m.kind} · {m.words.toLocaleString()} words ·{" "}
                      {humanize(m.status)}
                    </option>
                  ))}
                </Select>
              </div>
            )}

            <div className="space-y-2">
              <Checkbox
                label="Start from zero (research + write the master story)"
                checked={fromZero}
                disabled={noMaster}
                onChange={(e) => setFromZeroChoice(e.target.checked)}
                aria-describedby={`${ids}-zero-hint`}
              />
              <p id={`${ids}-zero-hint`} className="text-[11px] leading-4 text-muted-foreground">
                Research runs only when the case has no facts yet; an existing English master story is
                reused instead of written again.
              </p>
              {fromZero && (
                <TargetMinutesInput
                  id={`${ids}-target`}
                  value={targetMinutes}
                  onChange={setTargetMinutes}
                  range={range}
                  label="Master story length (minutes)"
                />
              )}
            </div>

            <RunOptionsFields settings={settings} value={options} onChange={setOptions} />

            <Button
              className="w-full"
              onClick={start}
              loading={busy === "start"}
              disabled={blockers.length > 0 || busy !== null}
            >
              <Play className="size-4" /> {options.mode === "pilot" ? "Start pilot" : "Start full film"}
            </Button>
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
  const percent = Math.round(job.progress * 100);
  const canCancel = job.status === "queued" || job.status === "running";
  const canResume =
    job.status === "failed" ||
    job.status === "cancelled" ||
    job.status === "partial" ||
    job.status === "interrupted";
  const languageErrors = Object.entries(job.result.errors ?? {});

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
            <Button variant="secondary" size="sm" onClick={onResume} loading={busy === "resume"} disabled={busy !== null}>
              <RotateCcw className="size-3.5" /> Resume
            </Button>
          )}
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
            ` · from zero${job.target_minutes ? ` (master story ${job.target_minutes} min)` : ""}`}{" "}
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
        <ol className="divide-y divide-border rounded-md border border-border">
          {job.stages.map((s) => {
            const detail = summarizeDetail(s.detail);
            const lastError = s.errors?.length ? s.errors[s.errors.length - 1] : null;
            const retried = (s.attempts ?? 0) > 1;
            return (
              <li key={s.name} className="flex items-start gap-2.5 px-3 py-2">
                <span className="mt-0.5">
                  <StageIcon status={s.status} />
                </span>
                <div className="min-w-0 flex-1">
                  <p
                    className={cn(
                      "text-sm",
                      (s.status === "pending" || s.status === "blocked") && "text-muted-foreground",
                    )}
                  >
                    {stageLabel(s.name)}
                  </p>
                  {detail && (
                    <p
                      className={cn(
                        "mt-0.5 break-words text-[11px] leading-4",
                        s.status === "failed" || s.status === "blocked"
                          ? "text-rose-600 dark:text-rose-400"
                          : s.status === "degraded"
                            ? "text-amber-700 dark:text-amber-400"
                            : "text-muted-foreground",
                      )}
                    >
                      {detail}
                    </p>
                  )}
                  {(retried || (lastError && s.status !== "failed")) && (
                    <p
                      className="mt-0.5 break-words text-[11px] leading-4 text-muted-foreground"
                      title={s.errors?.map((e) => `${e.at} · ${e.type}: ${e.message}`).join("\n")}
                    >
                      {retried && `${s.attempts} attempts`}
                      {lastError &&
                        s.status !== "failed" &&
                        `${retried ? " · " : ""}earlier: ${lastError.type} — ${lastError.message}`}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
