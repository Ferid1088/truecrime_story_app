"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { BellRing, Clapperboard, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, langLabel } from "@/lib/format";
import type { DocumentaryJob, DocumentaryMode, FollowUpCandidate } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import { cn } from "@/lib/utils";
import { FieldLabel, ResolutionChange, SourceLinks, filmTitle, pct } from "./shared";

/**
 * "PREVIOUSLY COVERED UNSOLVED CASE — NOW SOLVED": the monitor found that a
 * case the channel covered while unsolved has been solved. Nothing is
 * produced until the user approves.
 */
export function FollowUpCard({
  candidate: fu,
  onApproved,
  onDismissed,
}: {
  candidate: FollowUpCandidate;
  onApproved: (fu: FollowUpCandidate, job: DocumentaryJob) => void;
  onDismissed: (fu: FollowUpCandidate) => void;
}) {
  const [approveOpen, setApproveOpen] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const video = fu.original_video;
  const title = fu.case_title ?? `Case #${fu.case_id}`;

  async function dismiss() {
    setDismissing(true);
    setError(null);
    try {
      await api.dismissFollowUp(fu.id);
      onDismissed(fu);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDismissing(false);
    }
  }

  return (
    <Card className="border-emerald-500/40 bg-emerald-500/[0.04]">
      <CardContent className="space-y-3">
        <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
          <BellRing className="size-3.5" aria-hidden />
          Previously covered unsolved case — now solved
        </p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-base font-semibold leading-snug">
            <Link href={`/cases/${fu.case_id}`} className="hover:text-primary">
              {title}
            </Link>
          </h3>
          <span className="flex flex-wrap items-center gap-2">
            <ResolutionChange from={fu.previous_status} to={fu.new_status} size="lg" />
            {fu.confidence != null && (
              <span className="text-xs tabular-nums text-muted-foreground">confidence {pct(fu.confidence)}</span>
            )}
          </span>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-md border border-border bg-card px-3 py-2">
            <FieldLabel>Previous video</FieldLabel>
            {video ? (
              <>
                <p className="text-sm font-medium">{filmTitle(video)}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {video.episode_number != null ? `Episode ${video.episode_number}` : "No episode number"} ·{" "}
                  {video.published_at ? `published ${formatDate(video.published_at)}` : "not published"} ·{" "}
                  {langLabel(video.language)}
                </p>
                {video.youtube_url && (
                  <a
                    href={video.youtube_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <ExternalLink className="size-3" aria-hidden /> Watch on YouTube
                  </a>
                )}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">The original video record is missing.</p>
            )}
          </div>
          <div className="rounded-md border border-border bg-card px-3 py-2">
            <FieldLabel>Development</FieldLabel>
            <p className="text-sm leading-6 text-foreground/90">
              {fu.development || "The monitor did not record the development."}
            </p>
            {fu.status_check?.reason && (
              <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                Monitor: {fu.status_check.reason} · {formatDate(fu.status_check.created_at)}
              </p>
            )}
          </div>
        </div>

        {fu.sources.length > 0 && (
          <div>
            <FieldLabel>Sources</FieldLabel>
            <SourceLinks sources={fu.sources} />
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
          <p className="text-sm font-medium">{fu.question}</p>
          <div className="flex shrink-0 gap-2">
            <Button size="sm" onClick={() => setApproveOpen(true)} disabled={dismissing}>
              <Clapperboard className="size-3.5" /> Create update video
            </Button>
            <Button size="sm" variant="outline" onClick={dismiss} loading={dismissing}>
              Not now
            </Button>
          </div>
        </div>
        {error && <p className="text-xs text-rose-500">{error}</p>}
      </CardContent>

      <ApproveFollowUpDialog
        open={approveOpen}
        candidate={fu}
        onClose={() => setApproveOpen(false)}
        onApproved={(job) => {
          setApproveOpen(false);
          onApproved(fu, job);
        }}
      />
    </Card>
  );
}

const MODES: { value: DocumentaryMode; label: string; hint: string }[] = [
  { value: "pilot", label: "Pilot", hint: "Only the opening — judge it before the whole film." },
  { value: "full", label: "Full film", hint: "The whole update video." },
];

function ApproveFollowUpDialog({
  open,
  candidate,
  onClose,
  onApproved,
}: {
  open: boolean;
  candidate: FollowUpCandidate;
  onClose: () => void;
  onApproved: (job: DocumentaryJob) => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create update video"
      description={`${candidate.case_title ?? "This case"} — one follow-up documentary: research refreshes the new developments, then the film opens with the earlier episode and says the case is now solved.`}
    >
      {open && <ApproveForm candidate={candidate} onCancel={onClose} onApproved={onApproved} />}
    </Dialog>
  );
}

function ApproveForm({
  candidate,
  onCancel,
  onApproved,
}: {
  candidate: FollowUpCandidate;
  onCancel: () => void;
  onApproved: (job: DocumentaryJob) => void;
}) {
  const settings = useApi(() => api.documentarySettings(), []);
  const [mode, setMode] = useState<DocumentaryMode>("pilot");
  const [picked, setPicked] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = useId();

  const available = settings.data?.languages ?? [];
  const original = candidate.original_video?.language;
  const defaults = original && available.includes(original) ? [original] : available.slice(0, 1);
  const languages = picked ?? defaults;

  function toggle(lang: string, on: boolean) {
    setPicked(on ? [...languages.filter((l) => l !== lang), lang] : languages.filter((l) => l !== lang));
  }

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.approveFollowUp(candidate.id, {
        mode,
        languages: available.filter((l) => languages.includes(l)),
      });
      onApproved(res.job);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  if (settings.error) return <ErrorState message={settings.error} onRetry={settings.refetch} />;
  if (!settings.data) return <Skeleton className="h-40" />;

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Production</legend>
        <div role="radiogroup" aria-label="Production mode" className="grid grid-cols-2 gap-2">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={mode === m.value}
              onClick={() => setMode(m.value)}
              className={cn(
                "cursor-pointer rounded-md border px-3 py-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
                mode === m.value ? "border-primary bg-accent text-accent-foreground" : "border-border hover:bg-muted",
              )}
            >
              <span className="block text-sm font-medium">{m.label}</span>
              <span className="block text-[11px] leading-4 text-muted-foreground">{m.hint}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset aria-describedby={`${ids}-lang-hint`}>
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Languages</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {available.map((l) => (
            <Checkbox
              key={l}
              label={langLabel(l) + (l === original ? " (original video)" : "")}
              checked={languages.includes(l)}
              onChange={(e) => toggle(l, e.target.checked)}
            />
          ))}
        </div>
        <p id={`${ids}-lang-hint`} className="mt-1 text-[11px] leading-4 text-muted-foreground">
          The update in the original video&apos;s language becomes the follow-up video linked to it.
        </p>
      </fieldset>

      {error && <p className="text-xs text-rose-500">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={approve} loading={busy} disabled={languages.length === 0}>
          <Clapperboard className="size-3.5" /> Start update video
        </Button>
      </div>
    </div>
  );
}
