"use client";

import { useState } from "react";
import { Music } from "lucide-react";
import { musicApi, ApiError, type MusicSourceMode } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Card, CardContent } from "@/components/ui/card";

const OPTIONS: { mode: MusicSourceMode; label: string; hint: string }[] = [
  { mode: "create", label: "Create", hint: "Every film gets fresh tracks while a new variant can still be generated." },
  { mode: "library", label: "Library only", hint: "Only tracks already in the database. A moment without a fitting track stays silent." },
  {
    mode: "create_if_missing",
    label: "Library first, create if missing",
    hint: "Use the database; generate a track only when none exists for that kind and mood.",
  },
];

export function MusicSourceCard() {
  const { data, refetch } = useApi(() => musicApi.source(), [], { keepPrevious: true });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!data) return null;
  const total = Object.values(data.tracks_per_mood).reduce((a, b) => a + b, 0);

  async function choose(mode: MusicSourceMode) {
    setMessage(null);
    try {
      await musicApi.setSource(mode);
      setMessage({ ok: true, text: "Saved." });
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not save." });
    }
  }

  return (
    <Card className="mt-6">
      <CardContent className="space-y-3 py-4">
        <div className="flex items-center gap-2">
          <Music className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Music source</h2>
          <span className="text-xs text-muted-foreground">
            {total} tracks in the library across {Object.keys(data.tracks_per_mood).length} kind/mood groups
          </span>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          {OPTIONS.map((o) => (
            <label
              key={o.mode}
              className={`cursor-pointer rounded-md border p-3 text-sm ${data.mode === o.mode ? "border-primary" : "border-border"}`}
            >
              <input
                type="radio"
                name="music-source"
                className="mr-2"
                checked={data.mode === o.mode}
                onChange={() => choose(o.mode)}
              />
              {o.label}
              <p className="mt-1 text-xs text-muted-foreground">{o.hint}</p>
            </label>
          ))}
        </div>
        {message && (
          <p className={`text-xs ${message.ok ? "text-muted-foreground" : "text-destructive"}`}>{message.text}</p>
        )}
      </CardContent>
    </Card>
  );
}
