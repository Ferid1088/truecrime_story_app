"use client";

import { useState } from "react";
import { Music } from "lucide-react";
import { musicApi, ApiError, type MusicSourceMode } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

const OPTIONS: { mode: MusicSourceMode; label: string; hint: string }[] = [
  { mode: "create", label: "Create", hint: "Every film gets fresh tracks while a new variant can still be generated." },
  { mode: "library", label: "Library only", hint: "Only tracks already in the database. A moment without a fitting track stays silent." },
  {
    mode: "create_if_missing",
    label: "Library first, create if missing",
    hint: "The audio director picks by situation; a track is generated only for a moment where none fits.",
  },
];

const LIGHT: Record<string, "success" | "warning" | "danger"> = {
  green: "success",
  amber: "warning",
  red: "danger",
};

export function MusicSourceCard() {
  const { data, refetch } = useApi(() => musicApi.source(), [], { keepPrevious: true });
  const tracks = useApi(() => musicApi.tracks(), [], { keepPrevious: true });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!data) return null;
  const total = data.groups.reduce((a, g) => a + g.tracks, 0);
  const neverUsed = (tracks.data ?? []).filter((t) => t.usage_count === 0);

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
    <Card className="mt-6" data-testid="music-source-card">
      <CardContent className="space-y-4 py-4">
        <div className="flex items-center gap-2">
          <Music className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Music source</h2>
          <span className="text-xs text-muted-foreground">
            {total} tracks in the library, {neverUsed.length} never used
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

        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Tracks per kind and mood
          </h3>
          <table className="w-full text-xs">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-1 pr-2">Kind</th>
                <th className="pr-2">Mood</th>
                <th className="pr-2">Tracks</th>
                <th className="pr-2">Never used</th>
                <th className="pr-2">Uses</th>
                <th>Enough?</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={`${g.kind}/${g.mood}`} className="border-t border-border">
                  <td className="py-1 pr-2">{g.kind}</td>
                  <td className="pr-2">{g.mood}</td>
                  <td className="pr-2">{g.tracks}</td>
                  <td className="pr-2">{g.never_used}</td>
                  <td className="pr-2">{g.uses}</td>
                  <td>
                    {g.light ? (
                      <Badge variant={LIGHT[g.light]}>
                        {g.tracks}/{g.target}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground">no target</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Moments with no fitting track (last {data.films_covered} films)
          </h3>
          {data.misses.length === 0 ? (
            <p className="text-xs text-muted-foreground">None recorded.</p>
          ) : (
            <ul className="space-y-1 text-xs">
              {data.misses.map((m) => (
                <li key={`${m.kind}/${m.mood}`} className="rounded-md border border-border p-2">
                  <span className="font-medium">
                    {m.kind} / {m.mood}
                  </span>{" "}
                  — {m.count} moment{m.count === 1 ? "" : "s"} in {m.films.length} film
                  {m.films.length === 1 ? "" : "s"}, {m.created} track{m.created === 1 ? "" : "s"} created
                  <p className="text-muted-foreground">Needed: {m.situations.join(" · ")}</p>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tracks</h3>
          <ul className="space-y-1 text-xs">
            {(tracks.data ?? []).map((t) => (
              <li key={t.id} className="flex flex-wrap items-baseline gap-2 border-t border-border py-1">
                <span className="font-mono">{t.id}</span>
                <Badge variant={t.usage_count ? "success" : "warning"}>
                  {t.usage_count ? `used ${t.usage_count}×` : "never used"}
                </Badge>
                <span className="text-muted-foreground">{t.tags.join(", ")}</span>
                <span className="basis-full text-muted-foreground">{t.situation}</span>
              </li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
