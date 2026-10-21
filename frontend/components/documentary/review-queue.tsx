"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function ReviewQueuePage({ caseId }: { caseId: number }) {
  const { data, loading, error, refetch } = useApi(() => api.reviewQueue(caseId), [caseId]);
  const [index, setIndex] = useState(0);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const queue = data?.queue ?? [];
  const safeIndex = queue.length === 0 ? 0 : Math.min(index, queue.length - 1);
  const item = queue[safeIndex];

  const markUse = useCallback(async () => {
    if (!item || busy) return;
    setBusy(true);
    try {
      await api.updateVisual(item.asset.id, { verification: "verified" });
      setMessage(`Used ${item.asset.asset_id} for ${item.entity_name}.`);
      await refetch();
    } finally {
      setBusy(false);
    }
  }, [item, busy, refetch]);

  const reject = useCallback(async () => {
    if (!item || busy) return;
    setBusy(true);
    try {
      await api.updateVisual(item.asset.id, { verification: "rejected" });
      setMessage(`Rejected ${item.asset.asset_id}.`);
      await refetch();
    } finally {
      setBusy(false);
    }
  }, [item, busy, refetch]);

  const wrongPerson = useCallback(async () => {
    if (!item || busy || !item.entity_key) return;
    setBusy(true);
    try {
      await api.reviewQueueWrongPerson(caseId, item.asset.id, item.entity_key);
      setMessage(`Marked wrong person for ${item.entity_name} — added to its "not this" list.`);
      await refetch();
    } finally {
      setBusy(false);
    }
  }, [item, busy, caseId, refetch]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "a" || e.key === "A") void markUse();
      else if (e.key === "r" || e.key === "R") void reject();
      else if (e.key === "w" || e.key === "W") void wrongPerson();
      else if (e.key === "n" || e.key === "N") setIndex((i) => Math.min(i + 1, queue.length - 1));
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [markUse, reject, wrongPerson, queue.length]);

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
          {queue.length} left to review, sorted by how many beats they would fill
        </p>
        {message && (
          <p role="status" className="mt-2 text-sm text-emerald-700">
            {message}
          </p>
        )}
      </div>

      {queue.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="review-queue-empty">
          Nothing needs a human decision right now.
        </p>
      ) : item ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(280px,440px)_1fr]">
          <section className="flex min-h-[420px] items-center justify-center bg-muted/40 p-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={apiFileUrl(item.asset.image_url)}
              alt={item.asset.title ?? item.entity_name}
              className="max-h-[480px] max-w-full object-contain"
            />
          </section>
          <section className="space-y-5">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Should show</div>
              <div className="text-lg font-medium">{item.entity_name}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Beat{item.beats.length === 1 ? "" : "s"} it would fill ({item.beats_count})
              </div>
              <ul className="mt-1 space-y-1 text-sm" data-testid="review-queue-beats">
                {item.beats.length === 0 && <li className="text-muted-foreground">No beat currently needs it.</li>}
                {item.beats.map((b) => (
                  <li key={b.beat_id}>
                    <span className="font-mono text-xs text-muted-foreground">{b.beat_id}</span> {b.summary}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Verifier&apos;s reason</div>
              <p className="text-sm" data-testid="review-queue-reason">{item.verifier_reason}</p>
              {item.confidence != null && (
                <p className="text-xs text-muted-foreground">Confidence {item.confidence.toFixed(2)}</p>
              )}
            </div>
            {item.asset_codes.length > 1 && (
              <p className="text-xs text-muted-foreground">
                Grouped with {item.asset_codes.length - 1} near-duplicate(s): {item.asset_codes.slice(1).join(", ")}
              </p>
            )}

            <div className="flex flex-wrap gap-3">
              <Button type="button" onClick={() => void markUse()} disabled={busy}>
                Use <span className="ml-1 text-xs opacity-70">(A)</span>
              </Button>
              <Button type="button" variant="outline" onClick={() => void reject()} disabled={busy}>
                Reject <span className="ml-1 text-xs opacity-70">(R)</span>
              </Button>
              <Button type="button" variant="ghost" onClick={() => void wrongPerson()} disabled={busy || !item.entity_key}>
                Wrong person <span className="ml-1 text-xs opacity-70">(W)</span>
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
            <p className="text-xs text-muted-foreground">
              Keyboard: A = Use, R = Reject, W = Wrong person, N = Next.
            </p>
          </section>
        </div>
      ) : null}
    </main>
  );
}
