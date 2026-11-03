"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** One narration preview: the voice alone in story order, with the text of the block being spoken (click a block to jump to it). */
function Player({ name }: { name: string }) {
  const audio = useRef<HTMLAudioElement>(null);
  const active = useRef<HTMLButtonElement>(null);
  const [now, setNow] = useState(0);
  const preview = useApi(() => api.narrationPreview(name), [name]);

  const blocks = preview.data?.blocks ?? [];
  const current = blocks.findIndex((b) => now >= b.start && now < b.end);

  useEffect(() => {
    active.current?.scrollIntoView({ block: "nearest" });
  }, [current]);

  if (preview.error) return <ErrorState message={preview.error} onRetry={preview.refetch} />;
  if (!preview.data) return <Skeleton className="h-40 w-full" />;
  const p = preview.data;
  const lang = p.language;
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">{p.title}</p>
        <p className="text-xs text-muted-foreground">
          {clock(p.duration_seconds)} · {p.blocks.length} blocks · {p.cost.characters_sent.toLocaleString()} characters · €{p.cost.actual_eur.toFixed(2)} · {p.model_id}
        </p>
        <p className="text-xs text-muted-foreground">{p.source}. A separate preview: the film&apos;s own narration is not changed.</p>
      </div>
      <audio
        ref={audio}
        controls
        preload="metadata"
        className="w-full"
        src={api.narrationPreviewAudioUrl(name)}
        onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        aria-label="Narration preview"
      />
      <ol className="max-h-[28rem] space-y-1 overflow-y-auto" aria-label="Blocks in story order">
        {blocks.map((b, i) => (
          <li key={b.block_id}>
            <button
              type="button"
              ref={i === current ? active : undefined}
              onClick={() => {
                if (audio.current) {
                  audio.current.currentTime = b.start;
                  void audio.current.play();
                }
              }}
              className={`flex w-full items-start gap-3 rounded-md border px-3 py-1.5 text-start text-sm ${i === current ? "border-primary bg-primary/5" : "border-border"}`}
            >
              <span className="w-12 shrink-0 text-xs text-muted-foreground" dir="ltr">{clock(b.start)}</span>
              <span lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"} className="flex-1">{b.text}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The narration previews that exist: the newest one is open. */
export function NarrationPreviews() {
  const list = useApi(() => api.narrationPreviews(), []);
  const [chosen, setChosen] = useState<string | null>(null);
  if (list.error) return <ErrorState message={list.error} onRetry={list.refetch} />;
  if (!list.data) return <Skeleton className="h-40 w-full" />;
  if (list.data.length === 0) return <EmptyState title="No narration preview yet" description="A preview is voiced on request and kept apart from the film's narration." />;
  const name = chosen ?? list.data[0].name;
  return (
    <div className="space-y-3">
      {list.data.length > 1 && (
        <select className="h-8 rounded-md border bg-background px-2 text-sm" aria-label="Preview" value={name} onChange={(e) => setChosen(e.target.value)}>
          {list.data.map((p) => <option key={p.name} value={p.name}>{p.title}</option>)}
        </select>
      )}
      <Player key={name} name={name} />
    </div>
  );
}
