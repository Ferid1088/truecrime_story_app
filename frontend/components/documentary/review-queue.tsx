"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { ApiError, apiFileUrl, mvpApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import type { MvpCandidateGroup } from "@/lib/types";

const DECISIONS = ["provenance", "identity", "rights_decision", "ethics", "scene"] as const;
const DECISION_LABEL: Record<(typeof DECISIONS)[number], string> = {
  provenance: "Provenance",
  identity: "Identity / context",
  rights_decision: "Rights",
  ethics: "Ethics",
  scene: "Scene fit",
};

const SHOWN = [
  { key: "needs_review", label: "Not sure" },
  { key: "rejected", label: "Rejected" },
  { key: "approved", label: "Approved" },
  { key: "all", label: "All" },
] as const;

const SHOWN_TEXT: Record<(typeof SHOWN)[number]["key"], string> = {
  needs_review: "pictures the verifiers could not settle",
  rejected: "pictures the planner rejected",
  approved: "pictures that are approved",
  all: "every picture, whatever the agents decided",
};

const KINDS = [
  { key: "photo", label: "Pictures" },
  { key: "video", label: "Videos" },
] as const;

type Kind = (typeof KINDS)[number]["key"];
type Verdict = "approved" | "rejected";

/** What a picture is for: the entity it should show, or for footage cut for a bare moment, what the clip shows. */
const labelOf = (group: MvpCandidateGroup) =>
  group.entity ?? (group.function === "moment" ? group.title ?? group.requirement : group.requirement);

const isVideo = (group: MvpCandidateGroup) => group.asset_type === "video" || group.asset_type === "video_source";
const kindOf = (group: MvpCandidateGroup): Kind => (isVideo(group) ? "video" : "photo");

/** The part of the video the planner would use: the media fragment plays only that window. */
function clipUrl(group: MvpCandidateGroup): string {
  const base = apiFileUrl(`/api/visuals/${group.asset_id}/file`);
  if (group.clip_start == null && group.clip_end == null) return base;
  return `${base}#t=${group.clip_start ?? 0}${group.clip_end != null ? `,${group.clip_end}` : ""}`;
}

function verdictOf(group: MvpCandidateGroup, decided: Record<number, Verdict>): Verdict | undefined {
  if (decided[group.id]) return decided[group.id];
  return group.status === "approved" || group.status === "rejected" ? group.status : undefined;
}

const NOTE_MAX = 400;

/** The owner's note on one picture or clip: the planner takes it as true and places the picture by it. */
function OwnerNote({ caseId, item }: { caseId: number; item: MvpCandidateGroup }) {
  const [saved, setSaved] = useState(item.owner_note ?? "");
  const [draft, setDraft] = useState(item.owner_note ?? "");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const changed = draft.trim() !== saved.trim();

  async function save() {
    setBusy(true);
    try {
      const out = await mvpApi.saveNote(caseId, item.asset_id, draft);
      setSaved(out.owner_note ?? "");
      setDraft(out.owner_note ?? "");
      setStatus("Saved. The planner uses it the next time the film is directed.");
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : "Could not save the note.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="review-queue-note">
      <label htmlFor={`owner-note-${item.asset_id}`} className="text-xs uppercase tracking-wide text-muted-foreground">
        Your note for the planner
      </label>
      <Textarea
        id={`owner-note-${item.asset_id}`}
        value={draft}
        maxLength={NOTE_MAX}
        rows={3}
        placeholder="Who or what this shows, e.g. Ashley's sister, Kayla Keyt - the planner places the picture by it"
        onChange={(e) => { setDraft(e.target.value); setStatus(""); }}
        className="mt-1"
      />
      <div className="mt-1 flex items-center gap-3">
        <Button type="button" size="sm" variant="outline" onClick={() => void save()} disabled={busy || !changed}>
          Save note
        </Button>
        <span className="text-xs text-muted-foreground">{draft.length}/{NOTE_MAX}</span>
        {status && <span role="status" className="text-xs text-emerald-700">{status}</span>}
      </div>
    </div>
  );
}

export function ReviewQueuePage({ caseId }: { caseId: number }) {
  const [chosenShown, setShown] = useState<(typeof SHOWN)[number]["key"] | null>(null);
  const [chosenKind, setKind] = useState<Kind | null>(null);
  const [beat, setBeat] = useState<string | undefined>(undefined);
  const { data, loading, error, refetch } = useApi(() => mvpApi.candidates(caseId, "all", beat), [caseId, beat]);
  const { data: gaps, refetch: refetchGaps } = useApi(() => mvpApi.gaps(caseId), [caseId]);
  const [index, setIndex] = useState(0);
  const [decided, setDecided] = useState<Record<number, Verdict>>({});
  const [message, setMessage] = useState("");
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const [busy, setBusy] = useState(false);
  // the list is fixed while you work: a decision never reshuffles it or brings a picture back
  const loaded: MvpCandidateGroup[] = useMemo(() => data ?? [], [data]);
  // open on something to look at: "Not sure" if anything is, else "Rejected"; the kind that has items
  const shown = chosenShown ?? (loaded.some((g) => g.status === "needs_review") ? "needs_review" : "rejected");
  const everything = useMemo(
    () => (shown === "all" ? loaded : loaded.filter((g) => g.status === shown)),
    [loaded, shown],
  );
  const kindCount = (k: Kind) => everything.filter((g) => kindOf(g) === k).length;
  const kind = chosenKind ?? (kindCount("photo") === 0 && kindCount("video") > 0 ? "video" : "photo");
  const queue = useMemo(() => everything.filter((g) => kindOf(g) === kind), [everything, kind]);
  const safeIndex = queue.length === 0 ? 0 : Math.min(index, queue.length - 1);
  const item = queue[safeIndex];
  const left = queue.filter((g) => !decided[g.id]).length;

  const go = useCallback(
    (to: number) => setIndex(Math.max(0, Math.min(to, queue.length - 1))),
    [queue.length],
  );

  const nextOpen = useCallback(
    (from: number) => {
      const after = queue.findIndex((g, i) => i > from && !decided[g.id]);
      return after >= 0 ? after : Math.min(from + 1, queue.length - 1);
    },
    [queue, decided],
  );

  const decide = useCallback(
    async (decision: "approve" | "reject") => {
      if (!item || busy) return;
      const blocked = decision === "approve" && item.rights_blocked;
      if (blocked && !window.confirm(
        `The rights check blocked this picture (${item.rights_reason ?? "no licence"}). ` +
        "Approve it anyway on your own decision? It stays marked as needing a licence before publication.")) return;
      setBusy(true);
      try {
        const done = await mvpApi.decideGroup(caseId, item.candidate_ids, decision, blocked);
        setDecided((d) => ({ ...d, [item.id]: decision === "approve" ? "approved" : "rejected" }));
        const where = item.requirements.length > 1 ? `${item.requirements.length} requirements` : item.requirement;
        setMessage(`${decision === "approve" ? "Approved" : "Rejected"} ${item.asset_code} for ${where}.` +
          (done.skipped.length ? ` ${done.skipped.length} blocked by rights were left out.` : ""));
        setIndex(nextOpen(safeIndex));
        void refetchGaps();
      } catch (e) {
        setMessage(e instanceof ApiError ? e.message : "Could not save the decision.");
      } finally {
        setBusy(false);
      }
    },
    [item, busy, caseId, nextOpen, safeIndex, refetchGaps],
  );

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "a" || e.key === "A") void decide("approve");
      else if (e.key === "r" || e.key === "R") void decide("reject");
      else if (e.key === "n" || e.key === "N") go(safeIndex + 1);
      else if (e.key === "p" || e.key === "P") go(safeIndex - 1);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [decide, go, safeIndex]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [safeIndex, kind, shown]);

  function switchTo(next: (typeof SHOWN)[number]["key"], toBeat?: string, toKind: Kind | null = null) {
    setShown(next);
    setKind(toKind);
    setBeat(toBeat);
    setIndex(0);
    setDecided({});
    setMessage("");
  }

  if (loading && !data) return <Skeleton className="h-[620px]" />;
  if (error && !data) return <p className="p-8 text-sm text-rose-700">{error}</p>;

  const verdict = item ? verdictOf(item, decided) : undefined;
  const height = "lg:h-[calc(100vh-17rem)]";
  return (
    <main className="w-full px-6 py-5" data-testid="review-queue-page">
      <header className="mb-3 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div className="min-w-0">
          <Link
            href={`/documentary/${caseId}`}
            className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="h-4 w-4" /> Documentary
          </Link>
          <h1 className="text-xl font-semibold tracking-tight">Picture review</h1>
          <p className="mt-0.5 text-sm text-muted-foreground" data-testid="review-queue-counter">
            {queue.length > 0 && <span className="font-medium text-foreground">{safeIndex + 1} / {queue.length} · </span>}
            {left} left to review: {SHOWN_TEXT[shown]}
            {queue.length - left > 0 && ` · ${queue.length - left} decided`}
          </p>
          <p className="text-xs text-muted-foreground">
            Each picture is shown once, however many requirements it was offered for; your decision applies to all of them.
          </p>
          {message && <p role="status" className="mt-1 text-sm text-emerald-700">{message}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-2" data-testid="review-queue-kind">
            {KINDS.map((option) => (
              <Button
                key={option.key}
                type="button"
                size="sm"
                variant={kind === option.key ? "default" : "outline"}
                onClick={() => switchTo(shown, beat, option.key)}
              >
                {option.label} ({kindCount(option.key)})
              </Button>
            ))}
          </div>
          <span className="mx-1 h-5 w-px bg-border" aria-hidden />
          <div className="flex flex-wrap gap-2" data-testid="review-queue-shown">
            {SHOWN.map((option) => (
              <Button
                key={option.key}
                type="button"
                size="sm"
                variant={shown === option.key ? "default" : "outline"}
                onClick={() => switchTo(option.key)}
              >
                {option.label}
              </Button>
            ))}
          </div>
          <Button type="button" size="sm" variant="ghost" onClick={() => { setDecided({}); void refetch(); }}>
            Reload list
          </Button>
          {beat && (
            <Button type="button" size="sm" variant="ghost" onClick={() => switchTo(shown)}>
              All beats (now {beat})
            </Button>
          )}
        </div>
      </header>

      {gaps && gaps.gap_count > 0 && (
        <details className="mb-3 rounded border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-foreground" data-testid="review-queue-gaps">
          <summary className="cursor-pointer font-medium">
            {gaps.gap_count} moment(s), {gaps.gap_seconds} s of the film, still have no picture, video or graphic. The film is
            not made until they do - pick a beat to review its pictures.
          </summary>
          <div className="mt-2 flex max-h-28 flex-wrap gap-2 overflow-y-auto">
            {gaps.beats.map((b) => (
              <Button
                key={b.beat_id}
                type="button"
                size="sm"
                variant={beat === b.beat_id ? "default" : "outline"}
                onClick={() => switchTo(shown, b.beat_id)}
              >
                {b.beat_id} · {b.needs_review} not sure · {b.rejected} rejected
              </Button>
            ))}
          </div>
          <div className="mt-3 border-t border-amber-500/40 pt-2" data-testid="review-queue-cover">
            <p className="text-xs">
              Last resort, after every alternative: cover each remaining moment from its neighbours. Half of it goes to the
              picture before (held longer) and half to the picture after. A neighbour that is a video, map or graphic is not
              stretched; a photo that spoils nothing covers its half instead. It applies when you resume.
            </p>
            <Button
              type="button"
              size="sm"
              className="mt-2"
              variant={gaps.neighbour_cover.approved ? "default" : "outline"}
              onClick={async () => { await mvpApi.decideNeighbourCover(caseId, !gaps.neighbour_cover.approved); refetchGaps(); }}
            >
              {gaps.neighbour_cover.approved ? "Approved: cover from neighbours (click to withdraw)" : "Approve: cover the remaining moments from neighbours"}
            </Button>
          </div>
        </details>
      )}

      {queue.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground" data-testid="review-queue-empty">
          Nothing to show here right now. Try the other kind or another status tab.
        </p>
      ) : item ? (
        <div className="grid gap-4 lg:grid-cols-[clamp(240px,15vw,640px)_minmax(0,1fr)_clamp(340px,25vw,900px)]">
          <ol className={`max-h-80 space-y-1 overflow-y-auto pr-1 lg:max-h-none ${height}`} aria-label="Pictures to review" data-testid="review-queue-list">
            {queue.map((g, n) => {
              const v = verdictOf(g, decided);
              return (
                <li key={g.id}>
                  <button
                    type="button"
                    ref={n === safeIndex ? activeRef : undefined}
                    onClick={() => go(n)}
                    aria-current={n === safeIndex}
                    className={`flex w-full cursor-pointer items-center gap-2 rounded border p-1.5 text-left text-xs ${
                      n === safeIndex ? "border-primary bg-muted" : "border-border hover:bg-muted/50"
                    }`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={apiFileUrl(g.thumb_url)} alt="" loading="lazy" className="h-12 w-16 shrink-0 rounded-sm bg-muted object-cover" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{labelOf(g)}</span>
                      <span className="block truncate text-muted-foreground">{g.beat_id} · {n + 1}</span>
                    </span>
                    {v && (
                      <span className={`size-2 shrink-0 rounded-full ${v === "approved" ? "bg-emerald-500" : "bg-rose-500"}`}
                            title={v} aria-label={v} />
                    )}
                  </button>
                </li>
              );
            })}
          </ol>

          <section className={`flex h-[60vh] min-h-[320px] items-center justify-center rounded bg-muted/40 p-3 lg:h-[calc(100vh-17rem)]`}>
            {isVideo(item) ? (
              <video
                key={item.asset_id}
                src={clipUrl(item)}
                poster={apiFileUrl(item.thumb_url)}
                controls
                preload="metadata"
                className="h-full w-full object-contain"
                data-testid="review-queue-video"
              />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={apiFileUrl(`/api/visuals/${item.asset_id}/file`)}
                alt={item.title ?? item.asset_code}
                className="h-full w-full object-contain"
              />
            )}
          </section>

          <section className={`space-y-4 overflow-y-auto pr-1 ${height}`}>
            <div className="flex flex-wrap gap-3">
              <Button type="button" onClick={() => void decide("approve")} disabled={busy}>
                {item.rights_blocked ? "Approve anyway" : "Approve"} <span className="ml-1 text-xs opacity-70">(A)</span>
              </Button>
              <Button type="button" variant="outline" onClick={() => void decide("reject")} disabled={busy}>
                Reject <span className="ml-1 text-xs opacity-70">(R)</span>
              </Button>
              <Button type="button" variant="ghost" onClick={() => go(safeIndex - 1)} disabled={safeIndex <= 0}>
                Previous <span className="ml-1 text-xs opacity-70">(P)</span>
              </Button>
              <Button type="button" variant="ghost" onClick={() => go(safeIndex + 1)} disabled={safeIndex >= queue.length - 1}>
                Next <span className="ml-1 text-xs opacity-70">(N)</span>
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">Keyboard: A = Approve, R = Reject, N = Next, P = Previous.</p>
            <div>
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                Should show
                {verdict && (
                  <Badge variant={verdict === "approved" ? "success" : "danger"} data-testid="review-queue-verdict">
                    {verdict === "approved" ? "Approved" : "Rejected"} - you can change it
                  </Badge>
                )}
              </div>
              <div className="text-lg font-medium">{labelOf(item)}</div>
              {item.need && <p className="text-sm text-muted-foreground">{item.need}</p>}
              <p className="mt-1 text-xs text-muted-foreground" data-testid="review-queue-requirements">
                <span className="font-mono">{item.beat_id}</span> · needs {item.role_needed} · {item.function.replace("_", " ")}
                {item.requirements.length > 1 && (
                  <>
                    {" "}· also offered for {item.requirements.length - 1} more requirement(s) in{" "}
                    {[...new Set(item.requirements.map((r) => r.beat_id))].join(", ")}
                  </>
                )}
              </p>
            </div>
            <OwnerNote key={item.asset_id} caseId={caseId} item={item} />
            <div className="flex flex-wrap gap-2" data-testid="review-queue-decisions">
              {DECISIONS.map((key) => (
                <Badge key={key} variant="outline">
                  {DECISION_LABEL[key]}: {item[key]}
                </Badge>
              ))}
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Why it is open</div>
              <ul className="mt-1 list-disc pl-5 text-sm" data-testid="review-queue-reason">
                {item.reasons.length === 0 && <li>The verifiers did not agree.</li>}
                {item.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-muted-foreground">
                {item.source ?? "unknown source"} · rights {item.rights}
              </p>
              {item.rights_blocked && (
                <p className="mt-1 text-xs text-amber-700" data-testid="review-queue-rights-note">
                  The rights check blocked this picture{item.rights_reason ? `: ${item.rights_reason}` : ""}. You can
                  still approve it; you will be asked to confirm.
                </p>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
