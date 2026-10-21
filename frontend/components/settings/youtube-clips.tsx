"use client";

import { useState } from "react";
import { Clapperboard } from "lucide-react";
import { youtubeClipsApi, ApiError } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Card, CardContent } from "@/components/ui/card";

export function YouTubeClipsCard() {
  const overview = useApi(() => youtubeClipsApi.overview(), [], { keepPrevious: true });
  const [message, setMessage] = useState<string | null>(null);
  if (!overview.data) return null;

  async function toggle(enabled: boolean) {
    setMessage(null);
    try {
      await youtubeClipsApi.setEnabled(enabled);
      overview.refetch();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not save.");
    }
  }

  const o = overview.data;
  return (
    <Card className="mt-6" data-testid="youtube-clips-card">
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <Clapperboard className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">YouTube clip downloader</h2>
          <span className="text-xs text-muted-foreground">{o.enabled ? "on" : "off"}</span>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={o.enabled}
            onChange={(e) => toggle(e.target.checked)}
            aria-label="YouTube clip downloader"
          />
          <span>
            Download clips from YouTube videos of allowed sources
            <span className="block text-xs text-muted-foreground">
              Off until you turn it on. Only videos of official bodies (police, courts, prosecutors,
              government), a news agency&apos;s raw footage, witness or family videos and Creative Commons
              videos are downloaded, never another creator&apos;s edited film. Each clip is cut to the sentence
              it illustrates (at most {o.max_clip_seconds} s, all together at most{" "}
              {Math.round(o.max_share * 100)}% of a film), keeps a source record, shows its credit on screen and is
              stored once. Downloading from YouTube may go against YouTube&apos;s terms: that risk is yours.
            </span>
          </span>
        </label>
        {message && <p className="text-xs text-destructive">{message}</p>}
      </CardContent>
    </Card>
  );
}
