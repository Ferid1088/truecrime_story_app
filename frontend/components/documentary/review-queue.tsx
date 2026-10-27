"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { ApiError, apiFileUrl, mvpApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

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
] as const;

export function ReviewQueuePage({ caseId }: { caseId: number }) {
  const [shown, setShown] = useState<(typeof SHOWN)[number]["key"]>("needs_review");
  const [beat, setBeat] = useState<string | undefined>(undefined);
  const { data, loading, error, refetch } = useApi(() => mvpApi.candidates(caseId, shown, beat), [caseId, shown, beat]);
  const { data: gaps } = useApi(() => mvpApi.gaps(caseId), [caseId]);
  const [index, setIndex] = useState(0);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const queue = data ?? [];
  const safeIndex = queue.length === 0 ? 0 : Math.min(index, queue.length - 1);
  const item = queue[safeIndex];

  const decide = useCallback(
    async (decision: "approve" | "reject") => {
      if (!item || busy) return;
      setBusy(true);
      try {
        await mvpApi.decideCandidate(item.id, decision);
        setMessage(`${decision === "approve" ? "Approved" : "Rejected"} ${item.asset_code} for ${item.requirement}.`);
        await refetch();
      } catch (e) {
        setMessage(e instanceof ApiError ? e.message : "Could not save the decision.");
      } finally {
        setBusy(false);
      }
    },
    [item, busy, refetch],
  );

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "a" || e.key === "A") void decide("approve");
      else if (e.key === "r" || e.key === "R") void decide("reject");
      else if (e.key === "n" || e.key === "N") setIndex((i) => Math.min(i + 1, queue.length - 1));
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [decide, queue.length]);

  if (loading && !data) return <Skeleton className="h-[620px]" />;
  if (error && !data) return <p className="p-8 text-sm text-rose-700">{error}</p>;

  return (
    <main className="mx-auto max-w-5xl px-6 py-8" data-testid="review-queue-page">
      <div className="mb-6">
        <Link
          href={`/documentary/${caseId}`}
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-4 w-4" /> Documentary
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Picture review</h1>
        <p className="mt-1 text-sm text-muted-foreground" data-testid="review-queue-counter">
          {queue.length} left to review: {shown === "rejected" ? "pictures the planner rejected" : "pictures the verifiers could not settle"}
        </p>
        {message && (
          <p role="status" className="mt-2 text-sm text-emerald-700">
            {message}
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2" data-testid="review-queue-shown">
          {SHOWN.map((option) => (
            <Button
              key={option.key}
              type="button"
              size="sm"
              variant={shown === option.key ? "default" : "outline"}
              onClick={() => {
                setShown(option.key);
                setIndex(0);
              }}
            >
              {option.label}
            </Button>
          ))}
          {beat && (
            <Button type="button" size="sm" variant="ghost" onClick={() => setBeat(undefined)}>
              All beats (now {beat})
            </Button>
          )}
        </div>
        {gaps && gaps.gap_count > 0 && (
          <section className="mt-4 rounded border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-foreground" data-testid="review-queue-gaps">
            <p className="font-medium">
              {gaps.gap_count} moment(s), {gaps.gap_seconds} s of the film, still have no picture, video or graphic. The
              film is not made until they do: approve pictures for these beats, or leave them and resume to be asked again.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {gaps.beats.map((b) => (
                <Button
                  key={b.beat_id}
                  type="button"
                  size="sm"
                  variant={beat === b.beat_id ? "default" : "outline"}
                  onClick={() => {
                    setBeat(b.beat_id);
                    setIndex(0);
                  }}
                >
                  {b.beat_id} · {b.needs_review} not sure · {b.rejected} rejected
                </Button>
              ))}
            </div>
          </section>
        )}
      </div>

      {queue.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="review-queue-empty">
          Nothing to show here right now.
        </p>
      ) : item ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(280px,440px)_1fr]">
          <section className="flex min-h-[420px] items-center justify-center bg-muted/40 p-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={apiFileUrl(item.thumb_url)}
              alt={item.title ?? item.asset_code}
              className="max-h-[480px] max-w-full object-contain"
            />
          </section>
          <section className="space-y-5">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Should show</div>
              <div className="text-lg font-medium">{item.entity ?? item.requirement}</div>
              {item.need && <p className="text-sm text-muted-foreground">{item.need}</p>}
              <p className="mt-1 text-xs text-muted-foreground">
                <span className="font-mono">{item.beat_id}</span> · needs {item.role_needed} · {item.function.replace("_", " ")}
              </p>
            </div>
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
            </div>
            <div className="flex flex-wrap gap-3">
              <Button type="button" onClick={() => void decide("approve")} disabled={busy}>
                Approve <span className="ml-1 text-xs opacity-70">(A)</span>
              </Button>
              <Button type="button" variant="outline" onClick={() => void decide("reject")} disabled={busy}>
                Reject <span className="ml-1 text-xs opacity-70">(R)</span>
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setIndex((i) => Math.min(i + 1, queue.length - 1))}
                disabled={safeIndex >= queue.length - 1}
              >
                Next <span className="ml-1 text-xs opacity-70">(N)</span>
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">Keyboard: A = Approve, R = Reject, N = Next.</p>
          </section>
        </div>
      ) : null}
    </main>
  );
}
