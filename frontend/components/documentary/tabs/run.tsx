"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, PenLine, Play, RotateCcw, Square } from "lucide-react";
import { api } from "@/lib/api";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type {
  DocumentaryJob,
  DocumentaryMode,
  FilmMinutesRange,
  RenderProfile,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState } from "@/components/state";
import { cn } from "@/lib/utils";
import {
  type DocumentaryTabProps,
  InlineAlert,
  JobStatusBadge,
  StageIcon,
  filmLengthIssue,
  finishedStages,
  isJobActive,
  stageLabel,
  summarizeDetail,
} from "../shared";

const PILOT_MIN_SECONDS = 11;
const PILOT_MAX_SECONDS = 1800;

interface RunTabProps extends Omit<DocumentaryTabProps, "refreshKey"> {
  masterId: number | null;
  job: DocumentaryJob | null;
  onMasterChange: (versionId: number) => void;
  /** Follow this job (just started, cancelled or resumed). */
  onJobChange: (job: DocumentaryJob) => void;
  onRefresh: () => void;
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
  const [languages, setLanguages] = useState<string[]>(settings.languages);
  const [mode, setMode] = useState<DocumentaryMode>("pilot");
  const [pilotSeconds, setPilotSeconds] = useState(String(settings.pilot_seconds));
  const [profile, setProfile] = useState<RenderProfile>("preview");
  const [refreshVisuals, setRefreshVisuals] = useState(false);
  const [busy, setBusy] = useState<"start" | "cancel" | "resume" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewJobId, setViewJobId] = useState<number | null>(null);
  const ids = useId();

  if (overview.masters.length === 0) {
    return (
      <EmptyState
        title="No master story yet"
        description={`A documentary is produced from a finished master story that runs ${range.min}–${range.max} minutes. Write one in the Story Studio first.`}
        action={
          <Button size="sm" onClick={() => router.push(`/studio?case=${caseId}`)}>
            <PenLine className="size-3.5" /> Open Story Studio
          </Button>
        }
      />
    );
  }

  const pilotValue = Number(pilotSeconds);
  const pilotValid =
    pilotSeconds.trim() !== "" &&
    Number.isFinite(pilotValue) &&
    pilotValue >= PILOT_MIN_SECONDS &&
    pilotValue <= PILOT_MAX_SECONDS;
  const missingVoice = languages.filter((l) => !settings.voices[l]?.voice_id);
  const outOfRange = languages.filter((l) =>
    filmLengthIssue(overview.languages[l]?.estimated_film_minutes, range),
  );

  const blockers: string[] = [];
  if (masterId == null) blockers.push("Choose a master story.");
  if (languages.length === 0) blockers.push("Select at least one language.");
  if (missingVoice.length)
    blockers.push(`No narrator voice is configured for ${missingVoice.map(langLabel).join(", ")}.`);
  if (mode === "pilot" && !pilotValid)
    blockers.push(`Pilot length must be ${PILOT_MIN_SECONDS}–${PILOT_MAX_SECONDS} seconds.`);
  if (mode === "full" && outOfRange.length)
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

  function toggleLanguage(lang: string, on: boolean) {
    setLanguages((prev) =>
      settings.languages.filter((l) => (l === lang ? on : prev.includes(l))),
    );
  }

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
        master_version_id: masterId,
        languages,
        mode,
        pilot_seconds: mode === "pilot" ? pilotValue : null,
        render_profile: profile,
        refresh_visuals: refreshVisuals,
      }),
    );

  const profileRights = (p: RenderProfile) =>
    (settings.rights_profiles[p] ?? []).map(humanize).join(", ");

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

            <div>
              <label htmlFor={`${ids}-master`} className="mb-1 block text-xs font-medium text-muted-foreground">
                Master story
              </label>
              <Select
                id={`${ids}-master`}
                value={masterId ?? ""}
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

            <fieldset>
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Languages</legend>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                {settings.languages.map((l) => (
                  <div key={l}>
                    <Checkbox
                      label={langLabel(l)}
                      checked={languages.includes(l)}
                      onChange={(e) => toggleLanguage(l, e.target.checked)}
                    />
                    {!settings.voices[l]?.voice_id && (
                      <p className="mt-0.5 pl-5.5 text-[10px] text-amber-600 dark:text-amber-400">
                        no narrator voice
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Mode</legend>
              <Choice
                name={`${ids}-mode`}
                checked={mode === "pilot"}
                onSelect={() => setMode("pilot")}
                title="Pilot"
                description={`The opening ${pilotValid ? formatTimecode(pilotValue) : "minutes"} of the full film in every selected language.`}
              />
              <Choice
                name={`${ids}-mode`}
                checked={mode === "full"}
                onSelect={() => setMode("full")}
                title="Full film"
                description={`The complete ${range.min}–${range.max} minute film. Refused when the story is too short or too long.`}
              />
              {mode === "pilot" && (
                <div className="pt-1">
                  <label
                    htmlFor={`${ids}-pilot`}
                    className="mb-1 block text-xs font-medium text-muted-foreground"
                  >
                    Pilot length (seconds)
                  </label>
                  <Input
                    id={`${ids}-pilot`}
                    type="number"
                    inputMode="numeric"
                    min={PILOT_MIN_SECONDS}
                    max={PILOT_MAX_SECONDS}
                    step={10}
                    value={pilotSeconds}
                    aria-invalid={!pilotValid}
                    aria-describedby={`${ids}-pilot-hint`}
                    onChange={(e) => setPilotSeconds(e.target.value)}
                  />
                  <p id={`${ids}-pilot-hint`} className="mt-1 text-[11px] text-muted-foreground">
                    Default {formatTimecode(settings.pilot_seconds)} · allowed {PILOT_MIN_SECONDS}–
                    {PILOT_MAX_SECONDS} s
                  </p>
                </div>
              )}
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Render profile</legend>
              <Choice
                name={`${ids}-profile`}
                checked={profile === "preview"}
                onSelect={() => setProfile("preview")}
                title="Preview"
                description={`Internal review copy. Pictures with rights: ${profileRights("preview")}.`}
              />
              <Choice
                name={`${ids}-profile`}
                checked={profile === "publish"}
                onSelect={() => setProfile("publish")}
                title="Publish"
                description={`Only pictures cleared for publication: ${profileRights("publish")}.`}
              />
            </fieldset>

            <div className="space-y-1">
              <Checkbox
                label="Search and plan pictures again"
                checked={refreshVisuals}
                onChange={(e) => setRefreshVisuals(e.target.checked)}
                aria-describedby={`${ids}-refresh-hint`}
              />
              <p id={`${ids}-refresh-hint`} className="text-[11px] leading-4 text-muted-foreground">
                Off: reuse the existing visual plan (no new research or picture checks). On: new
                research, verification and shot direction — use after reviewing the Visual Library.
              </p>
            </div>

            <Button
              className="w-full"
              onClick={start}
              loading={busy === "start"}
              disabled={blockers.length > 0 || busy !== null}
            >
              <Play className="size-4" /> {mode === "pilot" ? "Start pilot" : "Start full film"}
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
                        {j.mode === "pilot" ? `Pilot ${formatTimecode(j.pilot_seconds)}` : "Full film"}
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

function Choice({
  name,
  checked,
  onSelect,
  title,
  description,
}: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  description: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-2.5 rounded-md border px-3 py-2 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/40",
        checked ? "border-primary bg-accent/40" : "border-border hover:bg-muted/50",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onSelect}
        className="mt-0.5 size-3.5 shrink-0 cursor-pointer accent-primary outline-none"
      />
      <span>
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-[11px] leading-4 text-muted-foreground">{description}</span>
      </span>
    </label>
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
  const canResume = job.status === "failed" || job.status === "cancelled";
  const now =
    job.status === "running" && job.stage
      ? `Now: ${stageLabel(job.stage)}`
      : job.status === "queued"
        ? "Waiting to start…"
        : job.status === "cancelling"
          ? "Stopping after the current stage…"
          : `${finishedStages(job)} of ${job.stages.length} stages finished`;

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle className="flex items-center gap-2">
          Job #{job.id} <JobStatusBadge status={job.status} />
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
        <p className="text-xs text-muted-foreground">
          {job.mode === "pilot" ? `Pilot · opening ${formatTimecode(job.pilot_seconds)}` : "Full film"} ·{" "}
          {job.languages.map(langLabel).join(", ")} · {job.render_profile} profile · started{" "}
          {formatDateTime(job.created_at)}
          {job.completed_at && ` · finished ${formatDateTime(job.completed_at)}`}
        </p>
        <div>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
            <span aria-live="polite">{now}</span>
            <span className="font-medium tabular-nums">{percent}%</span>
          </div>
          <div
            role="progressbar"
            aria-label={`Job ${job.id} progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <Progress
              value={percent}
              barClassName={
                job.status === "failed"
                  ? "bg-rose-500"
                  : job.status === "completed"
                    ? "bg-emerald-500"
                    : undefined
              }
            />
          </div>
        </div>
        {job.error && (
          <InlineAlert tone="error">
            <span className="font-medium">Error:</span> {job.error}
          </InlineAlert>
        )}
        <ol className="divide-y divide-border rounded-md border border-border">
          {job.stages.map((s) => {
            const detail = summarizeDetail(s.detail);
            return (
              <li key={s.name} className="flex items-start gap-2.5 px-3 py-2">
                <span className="mt-0.5">
                  <StageIcon status={s.status} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className={cn("text-sm", s.status === "pending" && "text-muted-foreground")}>
                    {stageLabel(s.name)}
                  </p>
                  {detail && (
                    <p
                      className={cn(
                        "mt-0.5 break-words text-[11px] leading-4",
                        s.status === "failed" ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground",
                      )}
                    >
                      {detail}
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
