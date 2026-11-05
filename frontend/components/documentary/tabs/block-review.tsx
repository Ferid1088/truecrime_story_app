"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { BlockReviewBlock, BlockReviewOverview, BlockReviewTake } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { BlockCard } from "./block-card";
import { ReplaceInBlocks } from "./block-replace";

const apiBase = (url: string) => apiFileUrl(url);

/** The hand review of a voiced narration: the blocks one after the other with their text and voice, played in a row from the first block (pause any time),
 *  corrected block by block (yellow block, red words), voiced again, ticked, assembled, approved. */
export function BlockReview({ versionId }: { versionId: number }) {
  const state = useApi(() => api.blockReview(versionId), [versionId]);
  const [local, setReview] = useState<BlockReviewOverview | null>(null);          // what the person changed here; before that, what the server sent
  const review = local ?? (state.data?.exists ? state.data : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState<number | null>(null);
  const [paused, setPaused] = useState(true);
  const [takePlaying, setTakePlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);

  const blocks = useMemo(() => review?.blocks ?? [], [review]);
  const activeUrl = (b: BlockReviewBlock) => b.takes.find((t) => t.active)?.audio_url;

  const play = useCallback((i: number) => {
    const el = audio.current;
    const url = blocks[i] && activeUrl(blocks[i]);
    if (!el || !url) return;
    setTakePlaying(null);
    setIndex(i);
    el.src = apiBase(url);
    void el.play();
    setPaused(false);
    document.getElementById(`block-${blocks[i].block_id}`)?.scrollIntoView({ block: "nearest" });
  }, [blocks]);

  function playTake(t: BlockReviewTake, blockId: string) {
    const el = audio.current;
    if (!el) return;
    const id = `${blockId}:${t.take_no}`;
    if (takePlaying === id && !el.paused) { el.pause(); setTakePlaying(null); return; }
    setIndex(null);
    setTakePlaying(id);
    el.src = apiBase(t.audio_url);
    void el.play();
    setPaused(false);
  }

  function toggle() {
    const el = audio.current;
    if (!el) return;
    if (index === null) { play(0); return; }
    if (el.paused) { void el.play(); setPaused(false); } else { el.pause(); setPaused(true); }
  }

  function onEnded() {
    if (index !== null && index + 1 < blocks.length) play(index + 1);
    else { setIndex(null); setPaused(true); setTakePlaying(null); }
  }

  const replace = (b: BlockReviewBlock) => {
    if (!review) return;
    const all = review.blocks.map((x) => (x.block_id === b.block_id ? b : x));
    setReview({ ...review, blocks: all, assembled_current: false, to_send: all.filter((x) => x.needs_sending).length, corrected_blocks: all.filter((x) => x.edited).length });
  };

  async function act<T extends BlockReviewOverview>(name: string, job: () => Promise<T>, done: (r: T) => string) {
    setBusy(name);
    setError(null);
    setNote(null);
    try {
      const r = await job();
      setReview(r);
      setNote(done(r));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const header = useMemo(() => {
    if (!review) return null;
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={review.approved ? "success" : "warning"} data-testid="review-state">{review.approved ? "approved" : review.assembled_current ? "assembled — not approved" : "in review"}</Badge>
        <span className="text-xs text-muted-foreground">{blocks.length} blocks · {review.corrected_blocks} corrected · {review.to_send} to send</span>
      </div>
    );
  }, [review, blocks.length]);

  if (state.error) return <ErrorState message={state.error} onRetry={state.refetch} />;
  if (!state.data) return <Skeleton className="h-40 w-full" />;
  if (!review) {
    const can = !state.data.exists && state.data.can_open;
    return (
      <EmptyState
        title="No voice review yet"
        description={can ? "The narration is made. Open the review to listen to its blocks and correct them by hand. Nothing is voiced or paid by opening it." : "The narration of this version is not made yet."}
        action={can ? <Button onClick={() => act("open", () => api.openBlockReview(versionId), () => "Review opened.")} disabled={busy !== null}>Open voice review</Button> : undefined}
      />
    );
  }

  return (
    <div className="space-y-3" data-testid="block-review">
      {header}
      <div className="sticky top-0 z-10 space-y-2 rounded-md border bg-background/95 p-3 backdrop-blur">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={toggle} aria-label={paused ? "Play from the first block" : "Pause"}>
            {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />} {paused ? (index === null ? "Play all" : "Resume") : "Pause"}
          </Button>
          <Button size="sm" variant="secondary" disabled={busy !== null || review.to_send === 0}
            onClick={() => act("send", async () => { const r = await api.sendAllBlocks(versionId); if (r.failed.length) setError(`${r.failed.length} block(s) failed: ${r.failed.map((f) => f.block_id).join(", ")}`); return r; }, (r) => `${r.sent.length} corrected block(s) voiced.`)}>
            {busy === "send" ? "Voicing…" : `Send ${review.to_send} corrected block${review.to_send === 1 ? "" : "s"} to ElevenLabs`}
          </Button>
          <Button size="sm" variant="secondary" disabled={busy !== null || review.to_send > 0}
            onClick={() => act("assemble", () => api.assembleBlockReview(versionId), (r) => `Assembled from the ticked takes${r.assembled_at ? "" : ""}. Listen to it, then approve.`)}>
            {busy === "assemble" ? "Assembling…" : "Check and assemble the narration"}
          </Button>
          <Button size="sm" disabled={busy !== null || !review.assembled_current || review.approved}
            onClick={() => {
              if (!window.confirm("Approve this narration? It becomes the film's narration and the film may go on. The earlier narration is kept.")) return;
              void act("approve", () => api.approveBlockReview(versionId), () => "Approved. The film may go on.");
            }}>
            Approve this narration
          </Button>
        </div>
        {review.assembled_audio_url && (
          <div className="text-xs text-muted-foreground">
            Assembled narration{review.assembled_current ? "" : " (out of date: ticked takes changed)"}:
            <audio controls preload="none" className="mt-1 w-full" src={apiBase(review.assembled_audio_url)} aria-label="Assembled narration" />
          </div>
        )}
        {note && <p role="status" className="text-sm text-emerald-700">{note}</p>}
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        <p className="text-xs text-muted-foreground">No film is rendered before you approve the narration.</p>
      </div>
      <ReplaceInBlocks versionId={versionId} onApplied={(r) => setReview(r)} />
      <audio ref={audio} preload="none" onEnded={onEnded} onPause={() => setPaused(true)} onPlay={() => setPaused(false)} aria-label="Block player" className="hidden" />
      <ol className="space-y-2" aria-label="Blocks in story order">
        {blocks.map((b, i) => (
          <BlockCard key={b.block_id} versionId={versionId} block={b} current={index === i} playingTake={takePlaying}
            onPlayBlock={() => play(i)} onPlayTake={(t) => playTake(t, b.block_id)} onChanged={replace} />
        ))}
      </ol>
    </div>
  );
}
