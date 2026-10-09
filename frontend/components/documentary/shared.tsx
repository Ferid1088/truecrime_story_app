"use client";

import { Fragment } from "react";
import { AlertTriangle, Ban, Check, CircleDashed, Loader2, SkipForward, X } from "lucide-react";
import type {
  DocumentaryJob,
  DocumentaryJobStatus,
  DocumentaryOverview,
  DocumentarySettings,
  DocumentaryStageStatus,
  FilmMinutesRange,
  VerificationStatus,
} from "@/lib/types";
import { humanize, isRtl, langLabel } from "@/lib/format";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Variant = NonNullable<BadgeProps["variant"]>;

/** What every documentary tab receives from the workspace. */
export interface DocumentaryTabProps {
  caseId: number;
  settings: DocumentarySettings;
  overview: DocumentaryOverview;
  /** Bumps whenever the workspace refreshes (manual or job progress). */
  refreshKey: number;
}

/** Tabs that follow the workspace-wide language choice. */
export interface LanguageTabProps extends DocumentaryTabProps {
  language: string;
  onLanguageChange: (lang: string) => void;
  /** Master story the overview was built for (production scripts belong to it). */
  masterId: number | null;
}

// ---------------------------------------------------------------------------
// jobs and stages
// ---------------------------------------------------------------------------

const ACTIVE_JOB_STATUSES: ReadonlySet<DocumentaryJobStatus> = new Set([
  "queued",
  "running",
  "cancelling",
]);

export function isJobActive(job: DocumentaryJob | null | undefined): boolean {
  return !!job && ACTIVE_JOB_STATUSES.has(job.status);
}

export function finishedStages(job: DocumentaryJob): number {
  return job.stages.filter((s) => s.status === "done" || s.status === "skipped" || s.status === "degraded")
    .length;
}

/** Stage names currently running — several at once, since stages run in parallel. */
export function runningStages(job: DocumentaryJob): string[] {
  return job.stages.filter((s) => s.status === "running").map((s) => s.name);
}

/** One line for what a job is doing right now. */
export function jobActivity(job: DocumentaryJob): string {
  if (job.status === "queued") return "Waiting for a free slot…";
  if (job.status === "cancelling") return "Stopping after the running stages…";
  const running = runningStages(job);
  if (job.status === "running" && running.length) return `Now: ${running.map(stageLabel).join(" · ")}`;
  return `${finishedStages(job)} of ${job.stages.length} stages finished`;
}

/** Progress bar colour by job status (running jobs keep the primary colour). */
export function jobProgressTone(status: DocumentaryJobStatus): string | undefined {
  if (status === "failed") return "bg-rose-500";
  if (status === "partial" || status === "interrupted") return "bg-amber-500";
  if (status === "completed") return "bg-emerald-500";
  return undefined;
}

const STAGE_LABELS: Record<string, string> = {
  research: "Research",
  master_story: "Master story",
  blueprint: "Editorial blueprint",
  audio_plan: "Audio plan",
  spoken: "Spoken version",
  film_length: "Film length check",
  visual_needs: "Visual needs",
  visual_research: "Visual research",
  visual_check: "Visual verification",
  visual_plan: "Visual plan",
  visual_gaps: "Production-time visual search",
  performance: "Narrator performance",
  voice: "Narration & music mix",
  production: "Production script",
  critique: "Critique & fixes",
  visual_audit: "Picture audit",
  render: "Render",
};

/** "spoken:fa" → "Spoken version · Persian" */
export function stageLabel(name: string): string {
  const [base, lang] = name.split(":");
  const label = STAGE_LABELS[base] ?? humanize(base);
  return lang ? `${label} · ${langLabel(lang)}` : label;
}

const JOB_STATUS: Record<DocumentaryJobStatus, { label: string; variant: Variant }> = {
  queued: { label: "Queued", variant: "info" },
  running: { label: "Running", variant: "warning" },
  cancelling: { label: "Cancelling", variant: "warning" },
  completed: { label: "Completed", variant: "success" },
  partial: { label: "Partial", variant: "warning" },
  failed: { label: "Failed", variant: "danger" },
  cancelled: { label: "Cancelled", variant: "outline" },
  interrupted: { label: "Interrupted", variant: "warning" },
};

export function JobStatusBadge({ status }: { status: DocumentaryJobStatus }) {
  const meta = JOB_STATUS[status] ?? { label: status, variant: "default" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

const STAGE_STATUS_TEXT: Record<DocumentaryStageStatus, string> = {
  pending: "Pending",
  running: "Running",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
  blocked: "Blocked — its language failed earlier",
  degraded: "Done with a gap — see the reason",
};

export function StageIcon({ status }: { status: DocumentaryStageStatus }) {
  const icon =
    status === "done" ? (
      <Check className="size-4 text-emerald-500" aria-hidden />
    ) : status === "running" ? (
      <Loader2 className="size-4 animate-spin text-amber-500" aria-hidden />
    ) : status === "failed" ? (
      <X className="size-4 text-rose-500" aria-hidden />
    ) : status === "skipped" ? (
      <SkipForward className="size-4 text-muted-foreground" aria-hidden />
    ) : status === "blocked" ? (
      <Ban className="size-4 text-rose-400/80" aria-hidden />
    ) : status === "degraded" ? (
      <AlertTriangle className="size-4 text-amber-500" aria-hidden />
    ) : (
      <CircleDashed className="size-4 text-muted-foreground" aria-hidden />
    );
  return (
    <span className="inline-flex shrink-0" title={STAGE_STATUS_TEXT[status]}>
      {icon}
      <span className="sr-only">{STAGE_STATUS_TEXT[status]}</span>
    </span>
  );
}

function detailValue(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(detailValue).filter(Boolean);
    return parts.length ? parts.join(", ") : null;
  }
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => {
        const s = detailValue(v);
        return s == null ? null : `${k} ${s}`;
      })
      .filter(Boolean);
    return parts.length ? parts.join(", ") : null;
  }
  return null;
}

/** One readable line for a stage's detail (result object or failure text). */
export function summarizeDetail(detail: unknown): string | null {
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (typeof detail !== "object" || Array.isArray(detail)) return detailValue(detail);
  const parts = Object.entries(detail as Record<string, unknown>)
    .map(([k, v]) => {
      const s = detailValue(v);
      return s == null ? null : `${humanize(k)}: ${s}`;
    })
    .filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

// ---------------------------------------------------------------------------
// film length
// ---------------------------------------------------------------------------

export function filmLengthIssue(
  minutes: number | null | undefined,
  range: FilmMinutesRange,
): "short" | "long" | null {
  if (minutes == null) return null;
  if (minutes < range.min) return "short";
  if (minutes > range.max) return "long";
  return null;
}

/** Languages (in configured order) whose latest spoken version has a production script. */
export function productionLanguages(languages: string[], overview: DocumentaryOverview): string[] {
  return languages.filter((l) => overview.languages[l]?.production);
}

// ---------------------------------------------------------------------------
// small presentational pieces
// ---------------------------------------------------------------------------

export function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "danger" | "success";
}) {
  return (
    <div className="rounded-md border border-border px-2.5 py-2">
      <p
        className={cn(
          "text-sm font-semibold tabular-nums leading-none",
          tone === "danger" && "text-rose-600 dark:text-rose-400",
          tone === "success" && "text-emerald-600 dark:text-emerald-400",
        )}
      >
        {value}
      </p>
      <p className="mt-1 text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}

export function InlineAlert({
  tone,
  children,
  className,
}: {
  tone: "error" | "warning";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn(
        "rounded-md border px-3 py-2 text-xs leading-5",
        tone === "error"
          ? "border-rose-500/30 bg-rose-500/5 text-rose-600 dark:text-rose-400"
          : "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * Segmented language switch. Languages without data stay visible but
 * disabled so the user sees what is missing.
 */
export function LanguagePicker({
  languages,
  value,
  onChange,
  isAvailable,
  label = "Language",
}: {
  languages: string[];
  value: string;
  onChange: (lang: string) => void;
  isAvailable?: (lang: string) => boolean;
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-md border border-border p-0.5">
      {languages.map((l) => {
        const available = isAvailable ? isAvailable(l) : true;
        return (
          <button
            key={l}
            type="button"
            aria-pressed={value === l}
            disabled={!available}
            title={available ? undefined : `Nothing produced in ${langLabel(l)} yet`}
            onClick={() => onChange(l)}
            className={cn(
              "cursor-pointer rounded px-2.5 py-1 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              value === l ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            {langLabel(l)}
          </button>
        );
      })}
    </div>
  );
}

/** Text in a story language: right-to-left for Persian/Arabic, with its lang tag. */
export function LangText({
  lang,
  children,
  className,
}: {
  lang: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"} className={cn("block", className)}>
      {children}
    </span>
  );
}

const LEVEL_VARIANT: Record<string, Variant> = { low: "outline", medium: "info", high: "accent" };

export function LevelBadge({ level }: { level: string }) {
  return <Badge variant={LEVEL_VARIANT[level] ?? "default"}>{level}</Badge>;
}

const ROLE_VARIANT: Record<string, Variant> = {
  evidence: "accent",
  context: "info",
  illustration: "default",
};

export function RoleBadge({ role }: { role: string }) {
  return <Badge variant={ROLE_VARIANT[role] ?? "default"}>{role}</Badge>;
}

const VERIFICATION_VARIANT: Record<VerificationStatus, Variant> = {
  verified: "success",
  needs_review: "warning",
  rejected: "danger",
  unverified: "outline",
};

export function VerificationBadge({
  status,
  confidence,
}: {
  status: VerificationStatus;
  confidence?: number | null;
}) {
  return (
    <Badge variant={VERIFICATION_VARIANT[status] ?? "default"}>
      {humanize(status)}
      {confidence != null && <span className="tabular-nums opacity-80">{Math.round(confidence * 100)}%</span>}
    </Badge>
  );
}

const RIGHTS_VARIANT: Record<string, Variant> = {
  owned: "success",
  licensed: "success",
  public_domain: "success",
  creative_commons: "info",
  editorial_review_required: "warning",
  permission_required: "danger",
  do_not_use: "danger",
  unknown: "outline",
};

export function RightsBadge({ rights }: { rights: string }) {
  return <Badge variant={RIGHTS_VARIANT[rights] ?? "outline"}>{humanize(rights)}</Badge>;
}

export const RIGHTS_OPTIONS = [
  "owned",
  "licensed",
  "public_domain",
  "creative_commons",
  "editorial_review_required",
  "permission_required",
  "unknown",
  "do_not_use",
] as const;

export const ROLE_OPTIONS = ["evidence", "context", "illustration"] as const;

export const VERIFICATION_OPTIONS = ["verified", "needs_review", "rejected", "unverified"] as const;

const AUDIO_TAG = /(\[[^[\]\n]{1,48}\])/g;

/** An ElevenLabs audio tag ([whispers]) — not spoken, changes how the next words are said. */
export function TagChip({ tag }: { tag: string }) {
  return (
    <span
      dir="ltr"
      className="mx-0.5 inline-flex items-center rounded border border-indigo-500/20 bg-indigo-500/10 px-1 align-baseline font-mono text-[10px] leading-4 text-indigo-700 dark:text-indigo-300"
    >
      {tag.startsWith("[") ? tag : `[${tag}]`}
    </span>
  );
}

/** Text sent to the voice, with its [audio tags] shown as chips. */
export function TtsText({ text }: { text: string }) {
  return (
    <>
      {/* split() with a capture group puts the tags at the odd indices */}
      {text.split(AUDIO_TAG).map((part, i) =>
        i % 2 === 1 ? <TagChip key={i} tag={part} /> : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  );
}
