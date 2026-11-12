"use client";

import { useState } from "react";
import { apiFileUrl, shortClipsApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl, langLabel } from "@/lib/format";
import type { ShortClipPart, ShortClipView } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const PARTS: { key: ShortClipPart; label: string }[] = [
  { key: "hook", label: "Hook" },
  { key: "context", label: "Context" },
  { key: "detail", label: "Detail" },
  { key: "reaction", label: "Reaction" },
  { key: "cta", label: "Invitation" },
];

const seconds = (value: number | null) => (value == null ? "" : `${value.toFixed(0)} s`);

/** One clip: its idea, the five spoken parts, the state of its voice and its video when one was made. */
function ClipCard({ clip, language }: { clip: ShortClipView; language: string }) {
  const dir = isRtl(language) ? "rtl" : "ltr";
  return (
    <li className="rounded-md border p-3" data-testid={`short-clip-${clip.clip_key}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{clip.clip_key}</span>
        {clip.angle && <Badge variant="outline">{clip.angle}</Badge>}
        <Badge variant={clip.voiced ? "success" : "warning"} data-testid="short-clip-voice">
          {clip.voiced ? `voiced ${seconds(clip.voice_seconds)}` : "not voiced yet"}
        </Badge>
        {clip.review && (
          <Badge variant={clip.review === "finished" ? "success" : "info"} data-testid="short-clip-review">
            {clip.review === "finished" ? "review finished" : "in review"}
          </Badge>
        )}
        <Badge variant={clip.video ? "success" : "default"} data-testid="short-clip-video-state">
          {clip.video ? `video ${seconds(clip.video.seconds)}` : "no video yet"}
        </Badge>
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
        <dl className="space-y-1.5">
          {PARTS.map((part) => (
            <div key={part.key} className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2 text-sm">
              <dt className="text-xs text-muted-foreground">{part.label}</dt>
              <dd lang={language} dir={dir}>{clip.parts[part.key]}</dd>
            </div>
          ))}
        </dl>
        {clip.video && (
          <video controls preload="none" className="aspect-[9/16] w-full max-w-[220px] rounded bg-black" src={apiFileUrl(clip.video.url)} aria-label={`Video of clip ${clip.clip_key}`} />
        )}
      </div>
    </li>
  );
}

/** Every short clip of the case, language by language: what is written, what is voiced and final, and the videos that exist. */
export function ShortClipsTab({ caseId }: { caseId: number }) {
  const state = useApi(() => shortClipsApi.overview(caseId), [caseId]);
  const [chosen, setChosen] = useState<string | null>(null);
  if (state.error && !state.data) return <ErrorState message={state.error} onRetry={state.refetch} />;
  if (!state.data) return <Skeleton className="h-64 w-full" />;
  const languages = state.data.languages;
  if (languages.length === 0) {
    return <EmptyState title="No short clips yet" description="Clips are planned after the English final text is finished (up to five ideas, about 25 seconds each)." />;
  }
  const current = languages.find((l) => l.language === chosen) ?? languages[0];
  const made = current.clips.filter((c) => c.video).length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Short clips</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Clip languages">
          {languages.map((l) => (
            <Button key={l.language} size="sm" variant={current.language === l.language ? "default" : "secondary"} onClick={() => setChosen(l.language)} data-testid={`short-clips-language-${l.language}`}>
              {langLabel(l.language)} ({l.clips.length})
            </Button>
          ))}
          <Button size="sm" variant="ghost" onClick={() => state.refetch()}>Refresh</Button>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="short-clips-summary">
          {current.clips.length} clips · {current.clips.filter((c) => c.voiced).length} voiced · {made} with a video
        </p>
        <ol className="space-y-3" aria-label={`Clips in ${langLabel(current.language)}`}>
          {current.clips.map((clip) => (
            <ClipCard key={clip.clip_key} clip={clip} language={current.language} />
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
